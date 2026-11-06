"""Local, profile-scoped evidence journal. No network or model access."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

PROFILES = ("orion", "aurora", "theo", "elysia", "selene")
KINDS = ("source_note", "inference", "question", "correction")
MANIFEST_LIMIT = 100000


def _manifest_json(manifest: dict) -> str:
    """Validate v1 inputs and encode them consistently within this Python API."""
    def nonempty(value, limit):
        return type(value) is str and bool(value.strip()) and len(value) <= limit

    if type(manifest) is not dict or set(manifest) != {"version", "question", "sources", "processing"}:
        raise ValueError("Manifest requires version, question, sources, and processing")
    if type(manifest["version"]) is not int or manifest["version"] != 1:
        raise ValueError("Only input manifest version 1 is supported")
    if not nonempty(manifest["question"], 20000):
        raise ValueError("Manifest question must be nonempty text, at most 20000 characters")
    sources = manifest["sources"]
    if type(sources) is not list or not 1 <= len(sources) <= 100:
        raise ValueError("Manifest requires an ordered list of 1 to 100 sources")
    for source in sources:
        if (type(source) is not dict or set(source) != {"locator", "content_sha256"}
                or not nonempty(source["locator"], 2000)
                or type(source["content_sha256"]) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", source["content_sha256"])):
            raise ValueError("Each source requires a locator and lowercase SHA-256 content digest")
    processing = manifest["processing"]
    if type(processing) is not dict or not nonempty(processing.get("pipeline"), 200):
        raise ValueError("Processing requires a nonempty pipeline version, at most 200 characters")

    remaining = 10000

    def validate(value, depth=0):
        nonlocal remaining
        remaining -= 1
        if depth > 20 or remaining < 0:
            raise ValueError("Manifest exceeds nesting or element limit")
        if type(value) is dict:
            for key, item in value.items():
                if type(key) is not str:
                    raise ValueError("Manifest object keys must be strings")
                validate(item, depth + 1)
        elif type(value) is list:
            for item in value:
                validate(item, depth + 1)
        elif type(value) not in (str, int, float, bool, type(None)):
            raise ValueError("Manifest values must be JSON types")

    validate(manifest)
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False)
    if len(encoded.encode("utf-8")) > MANIFEST_LIMIT:
        raise ValueError("Manifest exceeds 100000 UTF-8 bytes")
    return encoded


def _load_manifest(path: str) -> dict:
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Manifest JSON contains a duplicate object key")
            result[key] = value
        return result

    with open(path, "rb") as stream:
        content = stream.read(MANIFEST_LIMIT + 1)
    if len(content) > MANIFEST_LIMIT:
        raise ValueError("Manifest file exceeds 100000 bytes")
    try:
        return json.loads(content.decode("utf-8"), object_pairs_hook=unique_keys)
    except (UnicodeError, RecursionError) as error:
        raise ValueError("Manifest must be bounded UTF-8 JSON") from error


class MemoryBank:
    """Append-only through this interface; the local file owner retains control.

    Profile filters prevent accidental mixing, not access by another process
    that has the same operating-system file permissions.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY,
                profile TEXT NOT NULL,
                kind TEXT NOT NULL,
                text TEXT NOT NULL,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL,
                supersedes INTEGER REFERENCES memories(id),
                UNIQUE(profile, kind, text, source, supersedes)
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS profile_history ON memories(profile, id)")
            db.execute("""CREATE TABLE IF NOT EXISTS dependencies (
                child_id INTEGER NOT NULL REFERENCES memories(id),
                parent_id INTEGER NOT NULL REFERENCES memories(id),
                PRIMARY KEY(child_id, parent_id),
                CHECK(child_id != parent_id)
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS dependency_children ON dependencies(parent_id, child_id)")
            db.execute("""CREATE TABLE IF NOT EXISTS research_results (
                profile TEXT NOT NULL, run_id TEXT NOT NULL, step TEXT NOT NULL,
                manifest_json TEXT NOT NULL, manifest_sha256 TEXT NOT NULL,
                result_text TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(profile, run_id, step)
            )""")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _profile(profile: str) -> str:
        value = profile.strip().casefold()
        if value not in PROFILES:
            raise ValueError("Choose a configured profile: " + ", ".join(PROFILES))
        return value

    def add(self, profile: str, kind: str, text: str, source: str,
            supersedes: int | None = None, *, derived_from: tuple[int, ...] = ()) -> int:
        profile = self._profile(profile)
        text, source = text.strip(), source.strip()
        if kind not in KINDS or not text or not source:
            raise ValueError("A valid kind, nonempty text, and source reference are required")
        if len(text) > 20000 or len(source) > 2000:
            raise ValueError("Text or source exceeds the journal size limit")
        if (kind == "correction") != (supersedes is not None):
            raise ValueError("Corrections must supersede a record; other kinds cannot")
        if len(derived_from) > 100 or any(type(value) is not int or value <= 0 for value in derived_from):
            raise ValueError("Supply at most 100 positive integer dependency IDs")
        parents = tuple(sorted(set(derived_from)))
        with self._connect() as db:
            # Serialize duplicate checks and competing corrections across writers.
            db.execute("BEGIN IMMEDIATE")
            duplicate = db.execute(
                "SELECT id FROM memories WHERE profile=? AND kind=? AND text=? "
                "AND source=? AND supersedes IS ?",
                (profile, kind, text, source, supersedes),
            ).fetchone()
            if duplicate:
                existing = tuple(row[0] for row in db.execute(
                    "SELECT parent_id FROM dependencies WHERE child_id=? ORDER BY parent_id",
                    (duplicate["id"],),
                ))
                if existing != parents:
                    raise ValueError("Existing record has different dependencies; append a distinct record")
                return duplicate["id"]
            for parent_id in parents:
                parent = db.execute("SELECT profile FROM memories WHERE id=?", (parent_id,)).fetchone()
                if not parent or parent["profile"] != profile:
                    raise ValueError("Dependencies must exist in the same profile")
            if supersedes is not None:
                original = db.execute(
                    "SELECT profile FROM memories WHERE id=?", (supersedes,)
                ).fetchone()
                if not original or original["profile"] != profile:
                    raise ValueError("Correction target must exist in the same profile")
                if db.execute("SELECT 1 FROM memories WHERE supersedes=?", (supersedes,)).fetchone():
                    raise ValueError("Correct the latest replacement, not a superseded record")
            cursor = db.execute(
                "INSERT INTO memories(profile,kind,text,source,created_at,supersedes) "
                "VALUES(?,?,?,?,?,?)",
                (profile, kind, text, source, datetime.now(UTC).isoformat(), supersedes),
            )
            record_id = int(cursor.lastrowid)
            # Parents already exist; new-record-only edges cannot create cycles.
            db.executemany("INSERT INTO dependencies(child_id,parent_id) VALUES(?,?)",
                           ((record_id, parent_id) for parent_id in parents))
            return record_id

    @staticmethod
    def _records(db: sqlite3.Connection, profile: str, suffix: str,
                 parameters: tuple = ()) -> list[dict]:
        # suffix is fixed internal SQL; all user input is bound as parameters.
        rows = db.execute("""WITH RECURSIVE affected(id) AS (
            SELECT d.child_id FROM dependencies d
            JOIN memories child ON child.id=d.child_id
            WHERE child.profile=? AND EXISTS (
                SELECT 1 FROM memories c WHERE c.supersedes=d.parent_id AND c.profile=?
            )
            UNION
            SELECT d.child_id FROM dependencies d JOIN affected a ON d.parent_id=a.id
            JOIN memories child ON child.id=d.child_id WHERE child.profile=?
        )
        SELECT m.*, EXISTS(SELECT 1 FROM affected WHERE affected.id=m.id) AS needs_review,
            EXISTS(SELECT 1 FROM memories c WHERE c.supersedes=m.id) AS is_superseded,
            (SELECT group_concat(parent_id) FROM dependencies WHERE child_id=m.id) AS dependency_ids
        FROM memories m WHERE m.profile=? """ + suffix,
            (profile, profile, profile, profile, *parameters)).fetchall()
        records = []
        for row in rows:
            record = dict(row)
            record["derived_from"] = sorted(int(value) for value in
                                          (record.pop("dependency_ids") or "").split(",") if value)
            record["needs_review"] = bool(record["needs_review"])
            record["review_status"] = ("superseded" if record.pop("is_superseded") else
                                       "needs_review" if record["needs_review"] else "unverified")
            records.append(record)
        return records

    def recall(self, profile: str, query: str = "", limit: int = 20,
               history: bool = False, *, include_needs_review: bool = False) -> list[dict]:
        """Read unflagged current notes by default; unflagged does not mean verified.

        Opt in to unresolved notes for context, or use history for all versions.
        Query and limit apply to every mode; export provides the complete history.
        """
        profile = self._profile(profile)
        if not 1 <= limit <= 1000:
            raise ValueError("Limit must be between 1 and 1000")
        # instr performs literal matching: '%' and '_' are not wildcards.
        with self._connect() as db:
            return self._records(db, profile,
                "AND instr(lower(m.text), lower(?)) > 0 "
                "AND (? OR NOT EXISTS (SELECT 1 FROM memories c WHERE c.supersedes=m.id)) "
                "AND (? OR NOT EXISTS (SELECT 1 FROM affected a WHERE a.id=m.id)) "
                "ORDER BY m.id DESC LIMIT ?",
                (query, history, history or include_needs_review, limit))

    def export(self, profile: str) -> dict:
        profile = self._profile(profile)
        with self._connect() as db:
            records = self._records(db, profile, "ORDER BY m.id")
            results = [self._result(row) for row in db.execute(
                "SELECT * FROM research_results WHERE profile=? ORDER BY run_id, step", (profile,))]
        return {"format": "lumina-memory-v1", "profile": profile,
                "records": records, "results": results}

    def deletion_impact(self, profile: str, record_ids: tuple[int, ...], *,
                        expected_fingerprint: str | None = None) -> dict:
        """Preview recorded note descendants only; never delete or authorize removal."""
        profile = self._profile(profile)
        if expected_fingerprint is not None and (
                type(expected_fingerprint) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", expected_fingerprint)):
            raise ValueError("Expected fingerprint must be a lowercase SHA-256 hex digest")
        if (not 1 <= len(record_ids) <= 100
                or any(type(value) is not int or value <= 0 for value in record_ids)):
            raise ValueError("Supply 1 to 100 positive integer record IDs")
        roots = sorted(set(record_ids))
        slots = ",".join("?" for _ in roots)
        with self._connect() as db:
            # Validation, traversal, and unmapped-result count share a read snapshot.
            db.execute("BEGIN")
            found = db.execute(f"SELECT count(*) FROM memories WHERE profile=? AND id IN ({slots})",
                               (profile, *roots)).fetchone()[0]
            if found != len(roots):
                raise ValueError("Every record must exist in the requested profile")
            affected = [row[0] for row in db.execute(f"""WITH RECURSIVE
                edges(child,parent) AS (
                    SELECT child_id,parent_id FROM dependencies
                    UNION SELECT id,supersedes FROM memories WHERE supersedes IS NOT NULL
                ), affected(id) AS (
                    SELECT id FROM memories WHERE profile=? AND id IN ({slots})
                    UNION
                    SELECT e.child FROM edges e JOIN affected a ON e.parent=a.id
                    JOIN memories m ON m.id=e.child WHERE m.profile=?
                ) SELECT id FROM affected ORDER BY id""", (profile, *roots, profile))]
            unmapped = db.execute("SELECT count(*) FROM research_results WHERE profile=?",
                                  (profile,)).fetchone()[0]
            # Conservative: any stored change in this profile invalidates review.
            snapshot = {"version": 1, "profile": profile, "requested_ids": roots,
                "records": [dict(row) for row in db.execute(
                    "SELECT * FROM memories WHERE profile=? ORDER BY id", (profile,))],
                "dependencies": [list(row) for row in db.execute(
                    "SELECT d.child_id,d.parent_id FROM dependencies d "
                    "JOIN memories m ON m.id=d.child_id WHERE m.profile=? "
                    "ORDER BY d.child_id,d.parent_id", (profile,))],
                "results": [dict(row) for row in db.execute(
                    "SELECT * FROM research_results WHERE profile=? ORDER BY run_id,step", (profile,))]}
            encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            fingerprint = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
            if expected_fingerprint is not None and expected_fingerprint != fingerprint:
                raise ValueError("Deletion preview changed; recompute and review before proceeding")
        return {"profile": profile, "requested_ids": roots, "affected_ids": affected,
                "scope": "recorded_note_links_only", "unmapped_result_count": unmapped,
                "fingerprint": fingerprint}

    @staticmethod
    def _result(row: sqlite3.Row) -> dict:
        result = dict(row)
        result["manifest"] = json.loads(result.pop("manifest_json"))
        return result

    def _result_inputs(self, profile: str, run_id: str, step: str, manifest: dict) -> tuple:
        profile = self._profile(profile)
        if any(type(value) is not str or not value.strip() or len(value) > 200
               for value in (run_id, step)):
            raise ValueError("Run ID and step must be nonempty text, at most 200 characters")
        encoded = _manifest_json(manifest)
        return profile, run_id, step, encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _matching_result(db: sqlite3.Connection, inputs: tuple) -> sqlite3.Row | None:
        row = db.execute("SELECT * FROM research_results WHERE profile=? AND run_id=? AND step=?",
                         inputs[:3]).fetchone()
        if row and (row["manifest_json"], row["manifest_sha256"]) != inputs[3:]:
            raise ValueError("Stored result has different inputs; use a new run ID or step")
        return row

    def save_result(self, profile: str, run_id: str, step: str,
                    manifest: dict, result_text: str) -> dict:
        """Commit once; identical retries return the original, conflicts never overwrite."""
        inputs = self._result_inputs(profile, run_id, step, manifest)
        if type(result_text) is not str or not result_text.strip() or len(result_text) > 20000:
            raise ValueError("Result must be nonempty text, at most 20000 characters")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._matching_result(db, inputs)
            if row:
                if row["result_text"] != result_text:
                    raise ValueError("Stored result has different output; original retained")
                return self._result(row)
            db.execute("INSERT INTO research_results VALUES(?,?,?,?,?,?,?)",
                       (*inputs, result_text, datetime.now(UTC).isoformat()))
            return self._result(self._matching_result(db, inputs))

    def reuse_result(self, profile: str, run_id: str, step: str, manifest: dict) -> dict | None:
        """Return a saved result only for matching declared inputs; None means absent."""
        inputs = self._result_inputs(profile, run_id, step, manifest)
        with self._connect() as db:
            row = self._matching_result(db, inputs)
            return self._result(row) if row else None

    def result_progress(self, profile: str, run_id: str) -> dict:
        """Discover saved steps, not input validity or whole-task completion.

        Progress is derived from committed results, never a separate counter.
        Call reuse_result with current inputs before reusing any listed step.
        """
        profile = self._profile(profile)
        if type(run_id) is not str or not run_id.strip() or len(run_id) > 200:
            raise ValueError("Run ID must be nonempty text, at most 200 characters")
        with self._connect() as db:
            steps = [dict(row) for row in db.execute(
                "SELECT step, manifest_sha256, created_at FROM research_results "
                "WHERE profile=? AND run_id=? ORDER BY step", (profile, run_id))]
        return {"profile": profile, "run_id": run_id, "saved_steps": steps}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(Path.home() / ".lumina" / "memory.sqlite3"))
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add", help="Append a sourced note, question, inference, or correction")
    add.add_argument("profile", choices=PROFILES)
    add.add_argument("--kind", choices=KINDS, required=True)
    add.add_argument("--text", required=True)
    add.add_argument("--source", required=True)
    add.add_argument("--supersedes", type=int)
    add.add_argument("--derived-from", type=int, action="append", default=[],
                     help="Supporting record ID; repeat for multiple sources (maximum 100)")
    recall = commands.add_parser("recall", help="Read unflagged current records for one profile",
        description="Omit superseded and needs-review notes by default. Returned notes are unverified.")
    recall.add_argument("profile", choices=PROFILES)
    recall.add_argument("--query", default="")
    recall.add_argument("--limit", default=20, type=int)
    recall.add_argument("--history", action="store_true",
                        help="Include all versions and unresolved review notes; query/limit still apply")
    recall.add_argument("--include-needs-review", action="store_true",
                        help="Include unresolved current notes for context, with review markers")
    export = commands.add_parser("export", help="Write the complete profile history as JSON to stdout")
    export.add_argument("profile", choices=PROFILES)
    progress = commands.add_parser("progress", help="List saved result steps for one profile and run",
        description="Report committed result metadata only. Check current inputs with reuse-result before reuse.")
    progress.add_argument("profile", choices=PROFILES)
    progress.add_argument("run_id")
    impact = commands.add_parser("deletion-impact", help="Preview linked note IDs; does not delete",
        description="Read-only candidate preview. Excludes unlinked copies and cached result contents.")
    impact.add_argument("profile", choices=PROFILES)
    impact.add_argument("record_ids", type=int, nargs="+")
    impact.add_argument("--expected-fingerprint",
                        help="Reject if the reviewed profile snapshot or requested IDs changed")
    for name in ("save-result", "reuse-result"):
        command = commands.add_parser(name, help="Save or check a result against declared inputs")
        command.add_argument("profile", choices=PROFILES)
        command.add_argument("run_id")
        command.add_argument("step")
        command.add_argument("--manifest", required=True, help="Local UTF-8 JSON input manifest")
        if name == "save-result":
            command.add_argument("--result", required=True)
    args = parser.parse_args(argv)
    try:
        bank = MemoryBank(args.db)
        if args.command == "add":
            result = {"id": bank.add(args.profile, args.kind, args.text, args.source,
                                     args.supersedes, derived_from=tuple(args.derived_from))}
        elif args.command == "recall":
            result = bank.recall(args.profile, args.query, args.limit, args.history,
                                  include_needs_review=args.include_needs_review)
        elif args.command == "save-result":
            result = bank.save_result(args.profile, args.run_id, args.step,
                                      _load_manifest(args.manifest), args.result)
        elif args.command == "reuse-result":
            result = bank.reuse_result(args.profile, args.run_id, args.step,
                                       _load_manifest(args.manifest))
        elif args.command == "progress":
            result = bank.result_progress(args.profile, args.run_id)
        elif args.command == "deletion-impact":
            result = bank.deletion_impact(args.profile, tuple(args.record_ids),
                                          expected_fingerprint=args.expected_fingerprint)
        else:
            result = bank.export(args.profile)
    except (ValueError, UnicodeError, sqlite3.Error, OSError) as error:
        parser.exit(2, f"Memory journal error: {error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

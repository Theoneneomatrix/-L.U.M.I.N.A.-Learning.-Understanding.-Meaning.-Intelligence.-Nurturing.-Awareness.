"""Local, profile-scoped evidence journal. No network or model access."""
from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

PROFILES = ("orion", "aurora", "theo", "elysia", "selene")
KINDS = ("source_note", "inference", "question", "correction")


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
            supersedes: int | None = None) -> int:
        profile = self._profile(profile)
        text, source = text.strip(), source.strip()
        if kind not in KINDS or not text or not source:
            raise ValueError("A valid kind, nonempty text, and source reference are required")
        if len(text) > 20000 or len(source) > 2000:
            raise ValueError("Text or source exceeds the journal size limit")
        if (kind == "correction") != (supersedes is not None):
            raise ValueError("Corrections must supersede a record; other kinds cannot")
        with self._connect() as db:
            # Serialize duplicate checks and competing corrections across writers.
            db.execute("BEGIN IMMEDIATE")
            duplicate = db.execute(
                "SELECT id FROM memories WHERE profile=? AND kind=? AND text=? "
                "AND source=? AND supersedes IS ?",
                (profile, kind, text, source, supersedes),
            ).fetchone()
            if duplicate:
                return duplicate["id"]
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
            return int(cursor.lastrowid)

    def recall(self, profile: str, query: str = "", limit: int = 20,
               history: bool = False) -> list[dict]:
        profile = self._profile(profile)
        if not 1 <= limit <= 1000:
            raise ValueError("Limit must be between 1 and 1000")
        # instr performs literal matching: '%' and '_' are not wildcards.
        with self._connect() as db:
            rows = db.execute(
                "SELECT m.* FROM memories m WHERE m.profile=? "
                "AND instr(lower(m.text), lower(?)) > 0 "
                "AND (? OR NOT EXISTS (SELECT 1 FROM memories c WHERE c.supersedes=m.id)) "
                "ORDER BY m.id DESC LIMIT ?",
                (profile, query, history, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def export(self, profile: str) -> dict:
        profile = self._profile(profile)
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM memories WHERE profile=? ORDER BY id", (profile,)
            ).fetchall()
        return {"format": "lumina-memory-v1", "profile": profile,
                "records": [dict(row) for row in rows]}


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
    recall = commands.add_parser("recall", help="Read current records for one profile")
    recall.add_argument("profile", choices=PROFILES)
    recall.add_argument("--query", default="")
    recall.add_argument("--limit", default=20, type=int)
    recall.add_argument("--history", action="store_true")
    export = commands.add_parser("export", help="Write the complete profile history as JSON to stdout")
    export.add_argument("profile", choices=PROFILES)
    args = parser.parse_args(argv)
    try:
        bank = MemoryBank(args.db)
        if args.command == "add":
            result = {"id": bank.add(args.profile, args.kind, args.text, args.source, args.supersedes)}
        elif args.command == "recall":
            result = bank.recall(args.profile, args.query, args.limit, args.history)
        else:
            result = bank.export(args.profile)
    except (ValueError, sqlite3.Error, OSError) as error:
        parser.exit(2, f"Memory journal error: {error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

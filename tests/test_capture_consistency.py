"""Invented common-snapshot experiments, not a production capture interface."""
import hashlib
import json
import queue
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from lumina_orion.bound_restore import inspect_bound_restore
from lumina_orion.fixture_projection import project_fixture_declarations
from lumina_orion.history_binding import bind_removal_history
from lumina_orion.memory import MemoryBank, PROFILES
from lumina_orion.removal import RemovalIntentLedger
from lumina_orion.snapshot_binding import bind_snapshot_manifest


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_notes(db, profile):
    return dict(format="lumina-memory-v1", profile=profile,
                records=MemoryBank._records(db, profile, "ORDER BY m.id"),
                results=[MemoryBank._result(row) for row in db.execute(
                    "SELECT * FROM research_results WHERE profile=? ORDER BY run_id, step", (profile,))])


def read_requests(db, profile):
    return [RemovalIntentLedger._record(row) for row in db.execute(
        "SELECT * FROM removal_intents WHERE profile=? ORDER BY id", (profile,))]


def read_all(path):
    # Test fixture only: both projections share an explicit read transaction.
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        db.execute("BEGIN")
        return {p: dict(notes=read_notes(db, p), requests=read_requests(db, p)) for p in PROFILES}
    finally:
        db.close()


def project(profile, notes, requests):
    # Target meanings are invented fixture assignments, never inferred identities.
    mappings = [dict(note_id=row["id"], record_key="fixture:note:" + str(row["id"]),
                     target_key="fixture:" + row["text"], content_sha256=digest(row["text"]))
                for row in notes["records"]]
    return project_fixture_declarations(profile, notes, requests, mappings,
                                        snapshot_id="fixture:copy", history_boundary="fixture:boundary")


def report(profile, value):
    snapshot = json.dumps(value["snapshot"]).encode()
    history = json.dumps(value["history"]).encode()
    return inspect_bound_restore(profile, snapshot, bind_snapshot_manifest(profile, snapshot),
                                 history_payload=history, expected_history=bind_removal_history(profile, history))


def competing_write(path, profile, outcomes, retry):
    """One atomic invented note/request pair, using only this temporary database."""
    db = sqlite3.connect(path, timeout=0)
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO memories(profile,kind,text,source,created_at) VALUES(?,?,?,?,?)",
                   (profile, "source_note", "new é😀", "fixture:writer", "fixture:new-time"))
        db.execute("INSERT INTO removal_intents(version,profile,request_id,target_key,content_sha256,created_at) "
                   "VALUES(1,?,?,?,?,?)", (profile, "fixture:new", "fixture:new é😀",
                                          digest("new é😀"), "fixture:new-time"))
        try:
            db.commit()
        except sqlite3.OperationalError as error:
            if error.sqlite_errorcode != sqlite3.SQLITE_BUSY:
                raise
            outcomes.put(("busy", db.in_transaction))
            if not retry.wait(5):
                raise TimeoutError("Reader did not release the fixture transaction")
            db.commit()
        outcomes.put(("committed", db.in_transaction))
    except Exception as error:
        outcomes.put(("error", repr(error)))
    finally:
        db.close()


class CaptureConsistencyTests(unittest.TestCase):
    def seed(self, path, mode):
        bank = MemoryBank(path)
        ledger = RemovalIntentLedger(path)
        for profile in PROFILES:
            bank.add(profile, "source_note", "original é😀", "fixture:original")
            ledger.record(profile, "fixture:original", target_key="fixture:original é😀",
                          content_sha256=digest("original é😀"))
        with sqlite3.connect(path) as db:
            self.assertEqual(db.execute("PRAGMA journal_mode=" + mode).fetchone()[0], mode.lower())
        return bank, ledger, read_all(path)

    def assert_preserved(self, before, after, profile):
        for other in PROFILES:
            if other != profile:
                self.assertEqual(after[other], before[other])
        self.assertEqual(after[profile]["notes"]["records"][:-1], before[profile]["notes"]["records"])
        self.assertEqual(after[profile]["requests"][:-1], before[profile]["requests"])
        self.assertEqual(after[profile]["notes"]["results"], before[profile]["notes"]["results"])
        self.assertEqual(after[profile]["notes"]["records"][-1]["text"], "new é😀")
        self.assertEqual(after[profile]["requests"][-1]["request_id"], "fixture:new")

    def assert_restart(self, root, path, profile, value, expected_database):
        saved = root / "capture.json"
        saved.write_text(json.dumps(dict(profile=profile, value=value)), encoding="utf-8")
        original = saved.read_bytes()
        script = '''import json,sys
from pathlib import Path
sys.path.insert(0, sys.argv[3])
from test_capture_consistency import read_all, report
data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(json.dumps(dict(report=report(data["profile"], data["value"]), database=read_all(Path(sys.argv[2])))))
'''
        expected_report = report(profile, value)
        for _ in range(2):
            result = subprocess.run([sys.executable, "-c", script, str(saved), str(path),
                                     str(Path(__file__).resolve().parent)],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), dict(report=expected_report, database=expected_database))
        self.assertEqual(saved.read_bytes(), original)
        self.assertEqual(read_all(path), expected_database)

    def test_one_read_snapshot_during_competing_write_all_profiles_delete_wal(self):
        for mode in ("DELETE", "WAL"):
            for profile in PROFILES:
                with self.subTest(mode=mode, profile=profile), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    path = root / "journal.sqlite3"
                    _, _, before = self.seed(path, mode)
                    reader = sqlite3.connect(path)
                    reader.row_factory = sqlite3.Row
                    outcomes = queue.Queue()
                    retry = threading.Event()
                    writer = threading.Thread(target=competing_write, args=(path, profile, outcomes, retry))
                    try:
                        reader.execute("BEGIN")
                        notes = read_notes(reader, profile)  # First SELECT fixes the read snapshot.
                        self.assertTrue(reader.in_transaction)
                        writer.start()
                        first = outcomes.get(timeout=5)
                        # DELETE cannot commit yet; WAL can commit while the old reader remains active.
                        self.assertEqual(first, ("busy", True) if mode == "DELETE" else ("committed", False))
                        requests = read_requests(reader, profile)
                        self.assertEqual(dict(notes=notes, requests=requests), before[profile])
                        value = project(profile, notes, requests)
                        reader.commit()
                        retry.set()
                        if mode == "DELETE":
                            self.assertEqual(outcomes.get(timeout=5), ("committed", False))
                    finally:
                        reader.close()
                        retry.set()
                        if writer.ident is not None:
                            writer.join(timeout=6)
                            self.assertFalse(writer.is_alive(), "Fixture writer failed to stop")
                    after = read_all(path)
                    self.assert_preserved(before, after, profile)
                    self.assertEqual(len(value["snapshot"]["records"]), 1)
                    fresh = project(profile, after[profile]["notes"], after[profile]["requests"])
                    self.assertEqual(len(fresh["snapshot"]["records"]), 2)
                    for capture in (value, fresh):
                        inspection = report(profile, capture)
                        self.assertTrue(all(f["status"] == "requested_version_present" for f in inspection["findings"]))
                        self.assertEqual(inspection["freshness"], "not_assessed")
                        self.assertEqual(inspection["authority"], "caller_asserted_only")
                        self.assertEqual(inspection["reopening"], "not_assessed")
                    # A consistent saved capture survives restart, including its old contents and limits.
                    self.assert_restart(root, path, profile, value, after)

    def test_separate_reads_accept_inconsistent_capture_negative_control(self):
        for mode in ("DELETE", "WAL"):
            for profile in PROFILES:
                with self.subTest(mode=mode, profile=profile), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    path = root / "journal.sqlite3"
                    bank, ledger, before = self.seed(path, mode)
                    notes = bank.export(profile)  # This read has ended before the writer begins.
                    outcomes = queue.Queue()
                    competing_write(path, profile, outcomes, threading.Event())
                    self.assertEqual(outcomes.get(timeout=5), ("committed", False))
                    requests = ledger.intents(profile)  # New requests paired with old notes.
                    self.assertEqual(notes, before[profile]["notes"])
                    self.assertEqual(len(requests), 2)
                    value = project(profile, notes, requests)  # Individually valid schemas still pass.
                    inspection = report(profile, value)
                    statuses = {f["request_id"]: f["status"] for f in inspection["findings"]}
                    self.assertEqual(statuses, {"fixture:original": "requested_version_present",
                                                "fixture:new": "target_not_listed"})
                    self.assertEqual(inspection["declared_coverage"]["declaration"], "partial")
                    self.assertEqual(inspection["coverage"], "caller_supplied_only")
                    self.assertEqual(inspection["freshness"], "not_assessed")
                    self.assertEqual(inspection["reopening"], "not_assessed")
                    after = read_all(path)
                    self.assert_preserved(before, after, profile)
                    self.assert_restart(root, path, profile, value, after)


if __name__ == "__main__":
    unittest.main()

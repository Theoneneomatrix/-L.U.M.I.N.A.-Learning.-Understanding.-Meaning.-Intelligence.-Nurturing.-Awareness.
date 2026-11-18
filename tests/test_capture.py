import copy
import json
import queue
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from lumina_orion.capture import capture_journal
from lumina_orion.memory import MemoryBank, PROFILES
from lumina_orion.removal import RemovalIntentLedger
from test_capture_consistency import competing_write, digest, project, read_all, report


class JournalCaptureTests(unittest.TestCase):
    def seed(self, path, mode):
        bank = MemoryBank(path)
        ledger = RemovalIntentLedger(path)
        manifest = dict(version=1, question="Invented question", sources=[
            dict(locator="fixture:source", content_sha256="a" * 64)], processing=dict(pipeline="fixture:v1"))
        for profile in PROFILES:
            source = bank.add(profile, "source_note", "original é😀", "fixture:original")
            bank.add(profile, "inference", "Invented inference", "fixture:derived", derived_from=(source,))
            bank.add(profile, "correction", "Invented correction", "fixture:correction", source)
            bank.save_result(profile, "fixture:run", "fixture:step", manifest, "Invented result")
            ledger.record(profile, "fixture:original", target_key="fixture:original é😀",
                          content_sha256=digest("original é😀"))
        with sqlite3.connect(path) as db:
            self.assertEqual(db.execute("PRAGMA journal_mode=" + mode).fetchone()[0], mode.lower())
        return bank, ledger

    def test_exports_profiles_review_markers_originals_and_restart(self):
        script = '''import json,sys
from lumina_orion.capture import capture_journal
print(json.dumps(capture_journal(sys.argv[1], sys.argv[2])))
'''
        with tempfile.TemporaryDirectory() as directory:
            for mode in ("DELETE", "WAL"):
                path = Path(directory) / (mode + " #?mode=rw é.sqlite3")
                bank, ledger = self.seed(path, mode)
                before = read_all(path)
                original = path.read_bytes()
                for profile in PROFILES:
                    result = capture_journal(path, " " + profile.upper() + " ")
                    self.assertEqual(result["memory_export"], bank.export(profile))
                    self.assertEqual(result["saved_intents"], ledger.intents(profile))
                    self.assertEqual(result["consistency"], "single_read_transaction")
                    self.assertEqual(result["coverage"], "partial")
                    self.assertEqual(result["freshness"], "not_assessed")
                    self.assertEqual(result["authority"], "not_assessed")
                    records = result["memory_export"]["records"]
                    self.assertEqual([r["review_status"] for r in records],
                                     ["superseded", "needs_review", "unverified"])
                    self.assertEqual(records[1]["derived_from"], [records[0]["id"]])
                    for _ in range(2):
                        child = subprocess.run([sys.executable, "-c", script, str(path), profile],
                                               capture_output=True, text=True, timeout=10, check=True)
                        self.assertEqual(json.loads(child.stdout), result)
                    result["memory_export"]["records"].clear()
                    result["memory_export"]["results"][0]["manifest"].clear()
                    result["saved_intents"].clear()
                self.assertEqual(read_all(path), before)
                self.assertEqual(path.read_bytes(), original)

    def test_missing_file_and_invalid_profile_do_not_initialize_anything(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing" / "journal.sqlite3"
            for profile in (None, "", "unknown", "theo\n", 1):
                with self.assertRaises(ValueError):
                    capture_journal(path, profile)
            with self.assertRaises(sqlite3.OperationalError):
                capture_journal(path, "theo")
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_missing_tables_are_errors_not_empty_exports(self):
        with tempfile.TemporaryDirectory() as directory:
            for missing in ("memories", "dependencies", "research_results", "removal_intents"):
                path = Path(directory) / (missing + ".sqlite3")
                self.seed(path, "DELETE")
                with sqlite3.connect(path) as db:
                    db.execute("DROP TABLE " + missing)  # Invented temporary failure fixture only.
                original = path.read_bytes()
                with self.assertRaisesRegex(ValueError, missing):
                    capture_journal(path, "theo")
                self.assertEqual(path.read_bytes(), original)

    def test_empty_co_located_store_is_partial_but_separate_ledger_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.sqlite3"
            MemoryBank(path)
            RemovalIntentLedger(Path(directory) / "other.sqlite3")
            with self.assertRaisesRegex(ValueError, "removal_intents"):
                capture_journal(path, "theo")
            RemovalIntentLedger(path)
            result = capture_journal(path, "theo")
            self.assertEqual(result["memory_export"]["records"], [])
            self.assertEqual(result["memory_export"]["results"], [])
            self.assertEqual(result["saved_intents"], [])
            self.assertEqual(result["coverage"], "partial")
            self.assertEqual(result["authority"], "not_assessed")

    def test_connection_rejects_sql_writes_and_closes_after_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.sqlite3"
            self.seed(path, "DELETE")
            before = read_all(path)
            connections = []

            def attempt_write(db, *args):
                connections.append(db)
                db.execute("DELETE FROM memories")

            with patch.object(MemoryBank, "_records", side_effect=attempt_write):
                with self.assertRaises(sqlite3.OperationalError) as raised:
                    capture_journal(path, "theo")
            self.assertEqual(raised.exception.sqlite_errorcode, sqlite3.SQLITE_READONLY)
            with self.assertRaises(sqlite3.ProgrammingError):
                connections[0].execute("SELECT 1")
            self.assertEqual(read_all(path), before)
            self.assertEqual(capture_journal(path, "theo")["memory_export"], before["theo"]["notes"])

    def test_competing_writer_keeps_one_snapshot_all_profiles_delete_wal(self):
        original_records = MemoryBank._records
        for mode in ("DELETE", "WAL"):
            for profile in PROFILES:
                with self.subTest(mode=mode, profile=profile), tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "journal.sqlite3"
                    self.seed(path, mode)
                    before = read_all(path)
                    outcomes = queue.Queue()
                    retry = threading.Event()
                    writer = threading.Thread(target=competing_write, args=(path, profile, outcomes, retry))

                    def interleave(db, *args):
                        rows = original_records(db, *args)
                        self.assertTrue(db.in_transaction)
                        writer.start()
                        self.assertEqual(outcomes.get(timeout=5),
                                         ("busy", True) if mode == "DELETE" else ("committed", False))
                        return rows

                    try:
                        with patch.object(MemoryBank, "_records", side_effect=interleave):
                            captured = capture_journal(path, profile)
                        self.assertEqual(captured["memory_export"], before[profile]["notes"])
                        self.assertEqual(captured["saved_intents"], before[profile]["requests"])
                    finally:
                        retry.set()
                        if writer.ident is not None:
                            writer.join(timeout=6)
                            self.assertFalse(writer.is_alive())
                    if mode == "DELETE":
                        self.assertEqual(outcomes.get(timeout=5), ("committed", False))
                    after = read_all(path)
                    for other in PROFILES:
                        if other != profile:
                            self.assertEqual(after[other], before[other])
                    self.assertEqual(after[profile]["notes"]["records"][:-1], before[profile]["notes"]["records"])
                    self.assertEqual(after[profile]["requests"][:-1], before[profile]["requests"])
                    self.assertEqual(after[profile]["notes"]["results"], before[profile]["notes"]["results"])
                    fresh = capture_journal(path, profile)
                    self.assertEqual(len(fresh["memory_export"]["records"]), 4)
                    self.assertEqual(len(fresh["saved_intents"]), 2)
                    # Existing explicit mapping remains a separate fixture-only step.
                    inputs = copy.deepcopy(captured)
                    inspected = report(profile, project(profile, captured["memory_export"], captured["saved_intents"]))
                    self.assertEqual(captured, inputs)
                    self.assertEqual(inspected["findings"][0]["status"], "requested_version_present")
                    self.assertEqual(inspected["reopening"], "not_assessed")


if __name__ == "__main__":
    unittest.main()

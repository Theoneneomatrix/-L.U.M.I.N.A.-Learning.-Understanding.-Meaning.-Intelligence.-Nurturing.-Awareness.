"""Invented temporary fixtures only; no application deletion adapter is shipped."""
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from lumina_orion.memory import MemoryBank


def reconcile_fixture(path, root, fingerprint, after_review=lambda: None,
                      after_mutation=lambda: None):
    # Reserve the writer BEFORE the real preview opens its separate read connection.
    # No writes occur until that reader closes; never use this with real archives.
    with closing(sqlite3.connect(path, timeout=0)) as db, db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE")
        preview = MemoryBank(path).deletion_impact(
            "orion", (root,), expected_fingerprint=fingerprint)
        after_review()
        for record_id in reversed(preview["affected_ids"]):
            db.execute("DELETE FROM dependencies WHERE child_id=?", (record_id,))
            db.execute("DELETE FROM memories WHERE id=? AND profile='orion'", (record_id,))
        if db.execute("PRAGMA foreign_key_check").fetchall():
            raise AssertionError("Fixture foreign keys broken")
        after_mutation()


class AtomicFixtureTests(unittest.TestCase):
    def fixture(self, directory, mode):
        path = Path(directory) / (mode + ".sqlite3")
        bank = MemoryBank(path)
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute("PRAGMA journal_mode=" + mode).fetchone()[0], mode.lower())
        root = bank.add("orion", "source_note", "Invented root", "fixture:root")
        child = bank.add("orion", "inference", "Invented derivative", "fixture:child",
                         derived_from=(root,))
        bank.add("orion", "question", "Unrelated original", "fixture:kept")
        bank.add("aurora", "source_note", "Other profile original", "fixture:other")
        before = {p: bank.export(p) for p in ("orion", "aurora")}
        preview = bank.deletion_impact("orion", (root,))
        self.assertEqual(preview["affected_ids"], [root, child])
        return path, bank, root, before, preview

    def test_writer_exclusion_and_atomic_visibility_in_both_modes(self):
        for mode in ("DELETE", "WAL"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                path, bank, root, before, preview = self.fixture(directory, mode)
                probes = []

                def probe():
                    # Separate readers see committed originals at both boundaries.
                    self.assertEqual({p: bank.export(p) for p in before}, before)
                    with closing(sqlite3.connect(path, timeout=0)) as competitor:
                        with self.assertRaises(sqlite3.OperationalError) as caught:
                            competitor.execute("BEGIN IMMEDIATE")
                        self.assertEqual(caught.exception.sqlite_errorcode, sqlite3.SQLITE_BUSY)
                    probes.append(True)

                reconcile_fixture(path, root, preview["fingerprint"], probe, probe)
                self.assertEqual(len(probes), 2)
                reopened = MemoryBank(path)
                self.assertEqual(reopened.export("aurora"), before["aurora"])
                expected = [r for r in before["orion"]["records"]
                            if r["id"] not in preview["affected_ids"]]
                self.assertEqual(reopened.export("orion")["records"], expected)
                # Reservation is released after commit, including for other profiles.
                reopened.add("aurora", "question", "Writer proceeds", "fixture:after")

    def test_stale_review_and_injected_failure_rollback_preserve_originals(self):
        for mode in ("DELETE", "WAL"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                path, bank, root, before, preview = self.fixture(directory, mode)

                def fail():
                    raise RuntimeError("injected after fixture mutation")

                with self.assertRaisesRegex(RuntimeError, "injected"):
                    reconcile_fixture(path, root, preview["fingerprint"], after_mutation=fail)
                self.assertEqual({p: MemoryBank(path).export(p) for p in before}, before)
                bank.add("orion", "question", "Change after review", "fixture:late")
                changed = {p: bank.export(p) for p in before}
                with self.assertRaisesRegex(ValueError, "preview changed"):
                    reconcile_fixture(path, root, preview["fingerprint"], after_review=fail)
                self.assertEqual({p: MemoryBank(path).export(p) for p in before}, changed)
                bank.add("aurora", "question", "Lock released on rejection", "fixture:released")

    def test_process_exit_before_and_after_commit_reopens_consistently(self):
        for mode in ("DELETE", "WAL"):
            for commit in (False, True):
                with self.subTest(mode=mode, commit=commit), tempfile.TemporaryDirectory() as directory:
                    path, bank, root, before, preview = self.fixture(directory, mode)
                    script = (
                        "import os,runpy; f=runpy.run_path(" + repr(str(Path(__file__).resolve())) + "); "
                        "f['reconcile_fixture'](" + repr(str(path)) + "," + repr(root) + "," +
                        repr(preview["fingerprint"]) +
                        ("" if commit else ",after_mutation=lambda:os._exit(71)") + "); os._exit(72)"
                    )
                    result = subprocess.run([sys.executable, "-c", script], capture_output=True,
                                            text=True, timeout=10)
                    self.assertEqual(result.returncode, 72 if commit else 71, result.stderr)
                    reopened = MemoryBank(path)
                    self.assertEqual(reopened.export("aurora"), before["aurora"])
                    expected = dict(before["orion"])
                    if commit:
                        expected["records"] = [r for r in expected["records"]
                                               if r["id"] not in preview["affected_ids"]]
                    self.assertEqual(reopened.export("orion"), expected)
                    reopened.add("orion", "question", "Restart writer", "fixture:restart")


if __name__ == "__main__":
    unittest.main()

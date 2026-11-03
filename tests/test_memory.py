import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lumina_orion.memory import MemoryBank


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "memory.sqlite3"
        self.bank = MemoryBank(self.path)

    def test_persistence_and_profile_isolation(self):
        self.bank.add("orion", "question", "How can retrieval improve?", "session:1")
        self.bank.add("aurora", "inference", "Different perspective", "session:2")
        bank = MemoryBank(self.path)
        self.assertEqual(len(bank.recall("orion")), 1)
        self.assertEqual(bank.recall("orion", "Different"), [])
        self.assertEqual(bank.recall("theo"), [])
        self.assertEqual(len(bank.export("aurora")["records"]), 1)

    def test_corrections_preserve_history_and_replace_current_view(self):
        first = self.bank.add("orion", "inference", "Tentative explanation", "session:1")
        second = self.bank.add("orion", "correction", "Revised explanation", "source:2", first)
        self.assertEqual([m["id"] for m in self.bank.recall("orion")], [second])
        self.assertEqual(len(self.bank.recall("orion", history=True)), 2)
        self.assertEqual(len(self.bank.export("orion")["records"]), 2)
        with self.assertRaises(ValueError):
            self.bank.add("aurora", "correction", "Wrong profile", "source:3", second)
        with self.assertRaises(ValueError):
            self.bank.add("orion", "correction", "Competing correction", "source:4", first)
        third = self.bank.add("orion", "correction", "Final revision", "source:5", second)
        self.assertEqual([m["id"] for m in self.bank.recall("orion")], [third])

    def test_retry_does_not_duplicate_notes_or_corrections(self):
        args = ("orion", "source_note", "Evidence", "source:1")
        first = self.bank.add(*args)
        self.assertEqual(self.bank.add(*args), first)
        correction = ("orion", "correction", "Revised", "source:2", first)
        second = self.bank.add(*correction)
        self.assertEqual(self.bank.add(*correction), second)
        self.assertEqual(len(self.bank.export("orion")["records"]), 2)

    def test_requires_evidence_metadata_and_valid_correction_targets(self):
        invalid = [
            ("orion", "inference", "Note", ""),
            ("orion", "fact", "Note", "source:1"),
            ("unknown", "question", "Question", "source:1"),
            ("orion", "correction", "Revision", "source:1"),
            ("orion", "correction", "Revision", "source:1", 1234),
            ("orion", "question", "Question", "source:1", 1234),
        ]
        for args in invalid:
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.bank.add(*args)
        self.assertEqual(self.bank.export("orion")["records"], [])

    def test_literal_search_and_bounds(self):
        self.bank.add("orion", "question", "What does 50% mean?", "session:1")
        self.bank.add("orion", "question", "Another question", "session:1")
        self.assertEqual(len(self.bank.recall("orion", "%")), 1)
        self.assertEqual(len(self.bank.recall("orion", "QUESTION")), 1)
        for limit in (0, 1001):
            with self.assertRaises(ValueError):
                self.bank.recall("orion", limit=limit)

    def test_cli_round_trip_without_credentials(self):
        command = [sys.executable, "-m", "lumina_orion.memory", "--db", str(self.path)]
        added = subprocess.run(command + ["add", "orion", "--kind", "question",
            "--text", "How do we check sources?", "--source", "session:test"],
            check=True, text=True, capture_output=True)
        self.assertGreater(json.loads(added.stdout)["id"], 0)
        exported = subprocess.run(command + ["export", "orion"],
            check=True, text=True, capture_output=True)
        self.assertEqual(len(json.loads(exported.stdout)["records"]), 1)

    def test_transitive_review_survives_restart_and_preserves_originals(self):
        source = self.bank.add("orion", "source_note", "Original", "source:1")
        summary = self.bank.add("orion", "inference", "Summary", "session:1", derived_from=(source,))
        conclusion = self.bank.add("orion", "inference", "Conclusion", "session:2", derived_from=(summary,))
        unrelated = self.bank.add("orion", "question", "Unrelated", "session:3")
        self.assertFalse(any(r["needs_review"] for r in self.bank.recall("orion")))
        corrected = self.bank.add("orion", "correction", "Revised", "source:2", source)
        bank = MemoryBank(self.path)
        records = {r["id"]: r for r in bank.recall("orion")}
        self.assertNotIn(source, records)
        self.assertTrue(records[summary]["needs_review"])
        self.assertTrue(records[conclusion]["needs_review"])
        self.assertFalse(records[corrected]["needs_review"])
        self.assertFalse(records[unrelated]["needs_review"])
        exported = {r["id"]: r for r in bank.export("orion")["records"]}
        self.assertEqual(exported[source]["text"], "Original")
        self.assertEqual(exported[conclusion]["derived_from"], [summary])
        self.assertEqual(len(exported), 5)

    def test_missing_and_cross_profile_dependencies_do_not_write(self):
        other = self.bank.add("aurora", "source_note", "Private", "source:1")
        for parents in ((other,), (12345,), (True,), (0,), (-1,), (1,) * 101):
            with self.subTest(parents=parents), self.assertRaises(ValueError):
                self.bank.add("orion", "inference", "Unsupported", "session:1", derived_from=parents)
        self.assertEqual(self.bank.export("orion")["records"], [])
        self.assertEqual(len(self.bank.export("aurora")["records"]), 1)

    def test_provenance_is_normalized_and_immutable_on_retry(self):
        first = self.bank.add("orion", "source_note", "A", "source:1")
        second = self.bank.add("orion", "source_note", "B", "source:2")
        record = self.bank.add("orion", "inference", "Combined", "session:1",
                               derived_from=(second, first, first))
        self.assertEqual(self.bank.add("orion", "inference", "Combined", "session:1",
                                       derived_from=(first, second)), record)
        with self.assertRaises(ValueError):
            self.bank.add("orion", "inference", "Combined", "session:1", derived_from=(first,))
        self.assertEqual(self.bank.recall("orion")[0]["derived_from"], [first, second])
        self.assertEqual(len(self.bank.export("orion")["records"]), 3)

    def test_new_note_using_superseded_source_is_already_flagged(self):
        first = self.bank.add("orion", "source_note", "A", "source:1")
        revised = self.bank.add("orion", "correction", "B", "source:2", first)
        stale = self.bank.add("orion", "inference", "Old basis", "session:1", derived_from=(first,))
        current = self.bank.add("orion", "inference", "New basis", "session:2", derived_from=(revised,))
        records = {r["id"]: r for r in self.bank.recall("orion")}
        self.assertTrue(records[stale]["needs_review"])
        self.assertFalse(records[current]["needs_review"])
        self.bank.add("orion", "correction", "C", "source:3", revised)
        records = {r["id"]: r for r in self.bank.recall("orion")}
        self.assertTrue(records[current]["needs_review"])

    def test_failure_rolls_back_correction_and_dependency_together(self):
        source = self.bank.add("orion", "source_note", "Original", "source:1")
        dependent = self.bank.add("orion", "inference", "Summary", "session:1", derived_from=(source,))
        evidence = self.bank.add("orion", "source_note", "New evidence", "source:2")
        with sqlite3.connect(self.path) as db:
            db.execute("""CREATE TRIGGER reject_edge BEFORE INSERT ON dependencies
                        BEGIN SELECT RAISE(ABORT, 'injected failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.bank.add("orion", "correction", "Replacement", "source:3", source,
                          derived_from=(evidence,))
        records = {r["id"]: r for r in MemoryBank(self.path).recall("orion")}
        self.assertIn(source, records)
        self.assertFalse(records[dependent]["needs_review"])
        self.assertEqual(len(records), 3)

    def test_additive_upgrade_preserves_legacy_database(self):
        legacy_path = Path(self.directory.name) / "legacy.sqlite3"
        with sqlite3.connect(legacy_path) as db:
            db.execute("""CREATE TABLE memories (
                id INTEGER PRIMARY KEY, profile TEXT NOT NULL, kind TEXT NOT NULL,
                text TEXT NOT NULL, source TEXT NOT NULL, created_at TEXT NOT NULL,
                supersedes INTEGER REFERENCES memories(id),
                UNIQUE(profile,kind,text,source,supersedes))""")
            db.execute("INSERT INTO memories VALUES(7,'orion','question','Kept','session:old','2026-01-01',NULL)")
        upgraded = MemoryBank(legacy_path).export("orion")["records"]
        self.assertEqual(upgraded[0]["id"], 7)
        self.assertEqual(upgraded[0]["text"], "Kept")
        self.assertEqual(upgraded[0]["created_at"], "2026-01-01")
        self.assertEqual(upgraded[0]["derived_from"], [])
        self.assertFalse(upgraded[0]["needs_review"])

    def test_cli_records_dependencies_and_returns_review_markers(self):
        source = self.bank.add("orion", "source_note", "Evidence", "source:1")
        command = [sys.executable, "-m", "lumina_orion.memory", "--db", str(self.path)]
        subprocess.run(command + ["add", "orion", "--kind", "inference", "--text", "Summary",
                       "--source", "session:test", "--derived-from", str(source)],
                       check=True, capture_output=True, text=True)
        self.bank.add("orion", "correction", "Revised evidence", "source:2", source)
        result = subprocess.run(command + ["recall", "orion"],
                                check=True, capture_output=True, text=True)
        summary = next(r for r in json.loads(result.stdout) if r["text"] == "Summary")
        self.assertEqual(summary["derived_from"], [source])
        self.assertTrue(summary["needs_review"])


if __name__ == "__main__":
    unittest.main()

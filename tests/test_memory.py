import json
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


if __name__ == "__main__":
    unittest.main()

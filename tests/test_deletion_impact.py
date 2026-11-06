import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lumina_orion.memory import MemoryBank


class DeletionImpactTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "invented.sqlite3"
        self.bank = MemoryBank(self.path)
        self.source = self.bank.add("orion", "source_note", "Invented source", "fixture:source")
        self.parent = self.bank.add("orion", "source_note", "Other support", "fixture:other")
        self.summary = self.bank.add("orion", "inference", "Combined", "fixture:summary",
                                     derived_from=(self.source, self.parent))
        self.leaf = self.bank.add("orion", "inference", "Downstream", "fixture:leaf",
                                  derived_from=(self.summary,))
        self.revision = self.bank.add("orion", "correction", "Revised source", "fixture:revision", self.source)
        self.revised_summary = self.bank.add("orion", "correction", "Revised summary", "fixture:new",
                                             self.summary, derived_from=(self.revision,))
        self.other_profile = self.bank.add("aurora", "source_note", "Invented source", "fixture:source")
        self.unlinked = self.bank.add("orion", "inference", "Invented source copied without link", "fixture:copy")
        self.affected = [self.source, self.summary, self.leaf, self.revision, self.revised_summary]

    def test_preview_traverses_both_edge_types_preserves_originals_and_restarts(self):
        before = {p: self.bank.export(p) for p in ("orion", "aurora")}
        preview = MemoryBank(self.path).deletion_impact(" ORION ", (self.source, self.source))
        self.assertRegex(preview["fingerprint"], r"^[0-9a-f]{64}$")
        self.assertEqual(preview, {"profile": "orion", "requested_ids": [self.source],
                                   "affected_ids": self.affected, "scope": "recorded_note_links_only",
                                   "unmapped_result_count": 0, "fingerprint": preview["fingerprint"]})
        self.assertNotIn(self.parent, preview["affected_ids"])
        self.assertNotIn(self.unlinked, preview["affected_ids"])
        self.assertEqual(self.bank.deletion_impact("orion", (self.source, self.parent))["affected_ids"],
                         sorted(self.affected + [self.parent]))
        self.assertEqual({p: self.bank.export(p) for p in before}, before)

    def test_invalid_or_other_profile_roots_fail_without_changes(self):
        before = self.bank.export("orion")
        for roots in ((), (True,), (0,), (-1,), (99999,), (self.other_profile,),
                      (self.source, self.other_profile), (self.source,) * 101):
            with self.subTest(roots=roots), self.assertRaises(ValueError):
                self.bank.deletion_impact("orion", roots)
        self.assertEqual(self.bank.export("orion"), before)

    def test_cli_exposes_cached_result_gap_without_returning_contents(self):
        manifest = {"version": 1, "question": "Invented", "sources": [
            {"locator": "fixture:source", "content_sha256": "a" * 64}],
            "processing": {"pipeline": "fixture-v1"}}
        self.bank.save_result("orion", "run", "step", manifest, "Invented cached output")
        self.bank.save_result("aurora", "run", "step", manifest, "Other profile output")
        before = self.bank.export("orion")
        response = subprocess.run([sys.executable, "-m", "lumina_orion.memory", "--db", str(self.path),
            "deletion-impact", "orion", str(self.source)], check=True, capture_output=True, text=True, timeout=10)
        report = json.loads(response.stdout)
        self.assertEqual(report["unmapped_result_count"], 1)
        self.assertEqual(report["affected_ids"], self.affected)
        self.assertNotIn("Invented cached output", response.stdout)
        self.assertEqual(self.bank.export("orion"), before)

    def test_fresh_preview_includes_new_descendant(self):
        old = self.bank.deletion_impact("orion", (self.source,))
        added = self.bank.add("orion", "inference", "New descendant", "fixture:late",
                              derived_from=(self.leaf,))
        new = MemoryBank(self.path).deletion_impact("orion", (self.source,))
        self.assertNotIn(added, old["affected_ids"])
        self.assertEqual(new["affected_ids"], self.affected + [added])
        with self.assertRaisesRegex(ValueError, "preview changed"):
            self.bank.deletion_impact("orion", (self.source,), expected_fingerprint=old["fingerprint"])

    def test_fingerprint_restart_normalization_and_profile_isolation(self):
        old = self.bank.deletion_impact("orion", (self.source,))
        self.bank.add("aurora", "question", "Unrelated change", "fixture:other-profile")
        before = self.bank.export("orion")
        bank = MemoryBank(self.path)
        self.assertEqual(bank.deletion_impact(" ORION ", (self.source, self.source),
                         expected_fingerprint=old["fingerprint"]), old)
        for profile, roots in (("orion", (self.summary,)), ("aurora", (self.other_profile,))):
            with self.subTest(profile=profile), self.assertRaisesRegex(ValueError, "preview changed"):
                bank.deletion_impact(profile, roots, expected_fingerprint=old["fingerprint"])
        self.assertEqual(bank.export("orion"), before)

    def test_changed_content_and_links_with_same_ids_reject_review(self):
        old = self.bank.deletion_impact("orion", (self.source,))
        for change, undo in (
            (("UPDATE memories SET text=? WHERE id=?", ("Changed", self.summary)),
             ("UPDATE memories SET text=? WHERE id=?", ("Combined", self.summary))),
            (("DELETE FROM dependencies WHERE child_id=? AND parent_id=?", (self.summary, self.parent)),
             ("INSERT INTO dependencies VALUES (?,?)", (self.summary, self.parent))),
        ):
            with sqlite3.connect(self.path) as db:
                db.execute(*change)
            self.assertEqual(self.bank.deletion_impact("orion", (self.source,))["affected_ids"], self.affected)
            with self.assertRaisesRegex(ValueError, "preview changed"):
                self.bank.deletion_impact("orion", (self.source,), expected_fingerprint=old["fingerprint"])
            with sqlite3.connect(self.path) as db:
                db.execute(*undo)

    def test_cached_result_change_rejects_review_even_with_same_count(self):
        manifest = {"version": 1, "question": "Invented", "sources": [
            {"locator": "fixture:source", "content_sha256": "a" * 64}],
            "processing": {"pipeline": "fixture-v1"}}
        self.bank.save_result("orion", "run", "step", manifest, "Original")
        old = self.bank.deletion_impact("orion", (self.source,))
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE research_results SET result_text='Changed' WHERE profile='orion'")
        self.assertEqual(self.bank.deletion_impact("orion", (self.source,))["unmapped_result_count"], 1)
        with self.assertRaisesRegex(ValueError, "preview changed"):
            self.bank.deletion_impact("orion", (self.source,), expected_fingerprint=old["fingerprint"])

    def test_cli_fingerprint_success_conflict_and_validation_do_not_write(self):
        old = self.bank.deletion_impact("orion", (self.source,))
        command = [sys.executable, "-m", "lumina_orion.memory", "--db", str(self.path),
                   "deletion-impact", "orion", str(self.source), "--expected-fingerprint", old["fingerprint"]]
        response = subprocess.run(command, check=True, capture_output=True, text=True, timeout=10)
        self.assertEqual(json.loads(response.stdout), old)
        self.bank.add("orion", "question", "Same-profile unrelated change", "fixture:new")
        before = self.bank.export("orion")
        rejected = subprocess.run(command, capture_output=True, text=True, timeout=10)
        self.assertEqual(rejected.returncode, 2)
        self.assertEqual(rejected.stdout, "")
        self.assertIn("preview changed", rejected.stderr)
        for fingerprint in ("", "a" * 63, "A" * 64, True, 12):
            with self.subTest(fingerprint=fingerprint), self.assertRaises(ValueError):
                self.bank.deletion_impact("orion", (self.source,), expected_fingerprint=fingerprint)
        self.assertEqual(self.bank.export("orion"), before)

    def test_synthetic_reconciliation_and_old_backup_resurrection(self):
        # Fixture-only removal, NOT a shipped deletion method or production recipe.
        backup = Path(self.directory.name) / "invented-backup.sqlite3"
        restored = Path(self.directory.name) / "invented-restored.sqlite3"
        with sqlite3.connect(self.path) as source, sqlite3.connect(backup) as target:
            source.backup(target)
        before_other = self.bank.export("aurora")
        before_kept = [r for r in self.bank.export("orion")["records"] if r["id"] not in self.affected]
        preview = self.bank.deletion_impact("orion", (self.source,))
        with sqlite3.connect(self.path) as db:
            db.execute("PRAGMA foreign_keys=ON")
            for record_id in reversed(preview["affected_ids"]):
                db.execute("DELETE FROM dependencies WHERE child_id=?", (record_id,))
                db.execute("DELETE FROM memories WHERE id=? AND profile='orion'", (record_id,))
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
        after = MemoryBank(self.path)
        self.assertEqual(after.export("orion")["records"], before_kept)
        self.assertEqual(after.export("aurora"), before_other)
        # Restoring an old snapshot brings back the fixture's removed records.
        with sqlite3.connect(backup) as source, sqlite3.connect(restored) as target:
            source.backup(target)
        restored_bank = MemoryBank(restored)
        self.assertEqual(restored_bank.deletion_impact("orion", (self.source,)), preview)
        # An identical restored snapshot also matches: no rollback protection.
        self.assertEqual(restored_bank.deletion_impact("orion", (self.source,),
                         expected_fingerprint=preview["fingerprint"]), preview)
        self.assertEqual(restored_bank.export("aurora"), before_other)


if __name__ == "__main__":
    unittest.main()

import copy
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

from lumina_orion.memory import MemoryBank


class ResultTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "journal.sqlite3"
        self.bank = MemoryBank(self.path)
        self.manifest = {
            "version": 1,
            "question": "What does this synthetic source support?",
            "sources": [{"locator": "fixture:leaf-v1",
                         "content_sha256": hashlib.sha256(b"synthetic leaf").hexdigest()}],
            "processing": {"pipeline": "summary-v1", "options": {"length": 100, "language": "en"}},
        }

    def save(self, manifest=None, text="Tentative summary"):
        return self.bank.save_result("orion", "fixture-run", "summary",
                                     self.manifest if manifest is None else manifest, text)

    def test_identical_retry_and_reordered_keys_survive_restart(self):
        saved = self.save()
        reordered = json.loads(json.dumps(self.manifest, sort_keys=True))
        bank = MemoryBank(self.path)
        self.assertEqual(bank.reuse_result("orion", "fixture-run", "summary", reordered), saved)
        self.assertEqual(bank.save_result("orion", "fixture-run", "summary", reordered,
                                         "Tentative summary"), saved)
        self.assertEqual(bank.export("orion")["results"], [saved])
        self.assertEqual(len(saved["manifest_sha256"]), 64)

    def test_changed_inputs_conflict_on_read_and_write_without_overwriting(self):
        saved = self.save()
        changed = []
        for field, value in (("question", "Different question"), ("sources", []), ("processing", {})):
            manifest = copy.deepcopy(self.manifest)
            if field == "sources":
                manifest[field][0]["content_sha256"] = hashlib.sha256(b"changed source").hexdigest()
            elif field == "processing":
                manifest[field]["options"]["length"] = 200
            else:
                manifest[field] = value
            changed.append(manifest)
        relocated = copy.deepcopy(self.manifest)
        relocated["sources"][0]["locator"] = "fixture:another"
        changed.append(relocated)
        pipeline = copy.deepcopy(self.manifest)
        pipeline["processing"]["pipeline"] = "summary-v2"
        changed.append(pipeline)
        for manifest in changed:
            with self.subTest(manifest=manifest):
                with self.assertRaisesRegex(ValueError, "different inputs"):
                    self.bank.reuse_result("orion", "fixture-run", "summary", manifest)
                with self.assertRaisesRegex(ValueError, "different inputs"):
                    self.save(manifest)
        self.assertEqual(MemoryBank(self.path).export("orion")["results"], [saved])

    def test_same_inputs_different_output_is_explicit_conflict(self):
        saved = self.save()
        with self.assertRaisesRegex(ValueError, "different output"):
            self.save(text="A nondeterministic alternative")
        self.assertEqual(self.bank.export("orion")["results"], [saved])

    def test_profile_run_and_step_are_separate_and_original_notes_unchanged(self):
        self.bank.add("orion", "question", "Original note", "fixture:original")
        original = self.bank.export("orion")["records"]
        self.save()
        for profile, run_id, step in (("aurora", "fixture-run", "summary"),
                                      ("orion", "other-run", "summary"),
                                      ("orion", "fixture-run", "other-step")):
            self.assertIsNone(self.bank.reuse_result(profile, run_id, step, self.manifest))
        other = self.bank.save_result("aurora", "fixture-run", "summary", self.manifest, "Other lens")
        self.assertEqual(self.bank.export("aurora")["results"], [other])
        self.assertEqual(self.bank.export("orion")["records"], original)
        self.assertEqual(self.bank.export("theo")["results"], [])
        revised = copy.deepcopy(self.manifest)
        revised["question"] = "New question"
        self.bank.save_result("orion", "new-run", "summary", revised, "New result")
        self.assertEqual(len(self.bank.export("orion")["results"]), 2)

    def test_source_order_is_an_input(self):
        self.manifest["sources"].append({"locator": "fixture:second", "content_sha256": "a" * 64})
        self.save()
        self.manifest["sources"].reverse()
        with self.assertRaisesRegex(ValueError, "different inputs"):
            self.bank.reuse_result("orion", "fixture-run", "summary", self.manifest)

    def test_invalid_manifests_never_write(self):
        invalid = [None, {}, {**self.manifest, "version": True},
                   {**self.manifest, "version": 2}, {**self.manifest, "extra": "unversioned"},
                   {**self.manifest, "question": " "}, {**self.manifest, "sources": []},
                   {**self.manifest, "sources": self.manifest["sources"] * 101},
                   {**self.manifest, "sources": [{"locator": "fixture:x", "content_sha256": "A" * 64}]},
                   {**self.manifest, "processing": {"pipeline": ""}}]
        for value in (float("nan"), float("inf"), ("tuple",), {1: "non-string key"}, object(), "x" * 100000):
            invalid.append({**self.manifest, "processing": {"pipeline": "v1", "options": value}})
        deep = []
        for _ in range(22):
            deep = [deep]
        invalid.append({**self.manifest, "processing": {"pipeline": "v1", "options": deep}})
        for manifest in invalid:
            with self.subTest(manifest=repr(manifest)[:100]), self.assertRaises(ValueError):
                self.bank.save_result("orion", "fixture-run", "summary", manifest, "Result")
        self.assertEqual(self.bank.export("orion")["results"], [])

    def test_invalid_identity_and_output_never_write(self):
        for profile, run_id, step in (("unknown", "run", "step"), ("orion", "", "step"),
                                      ("orion", "run", "x" * 201), ("orion", True, "step")):
            with self.subTest(profile=profile, run_id=run_id), self.assertRaises(ValueError):
                self.bank.save_result(profile, run_id, step, self.manifest, "Result")
        for text in (None, " ", "x" * 20001):
            with self.assertRaises(ValueError):
                self.save(text=text)
        self.assertEqual(self.bank.export("orion")["results"], [])

    def test_competing_writers_keep_one_original(self):
        barrier = Barrier(2)

        def write(text):
            barrier.wait(timeout=10)
            try:
                return self.save(text=text)["result_text"]
            except ValueError:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(write, ("First candidate", "Second candidate")))
        self.assertEqual(outcomes.count("conflict"), 1)
        results = MemoryBank(self.path).export("orion")["results"]
        self.assertEqual(len(results), 1)
        self.assertIn(results[0]["result_text"], outcomes)

    def test_failed_insert_leaves_journal_unchanged(self):
        self.bank.add("orion", "question", "Original", "fixture:kept")
        before = self.bank.export("orion")
        with sqlite3.connect(self.path) as db:
            db.execute("""CREATE TRIGGER reject_result BEFORE INSERT ON research_results
                        BEGIN SELECT RAISE(ABORT, 'injected failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.save()
        self.assertEqual(MemoryBank(self.path).export("orion"), before)

    def test_cli_resume_after_abrupt_exit_and_conflict(self):
        manifest_path = Path(self.directory.name) / "inputs.json"
        manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        worker = (
            "import json, os, sys; from lumina_orion.memory import MemoryBank; "
            "manifest=json.loads(open(sys.argv[2], encoding='utf-8').read()); "
            "MemoryBank(sys.argv[1]).save_result('orion','fixture-run','summary',manifest,'Saved'); "
            "os._exit(7)"
        )
        exited = subprocess.run([sys.executable, "-c", worker, str(self.path), str(manifest_path)],
                                capture_output=True, text=True)
        self.assertEqual(exited.returncode, 7, exited.stderr)
        command = [sys.executable, "-m", "lumina_orion.memory", "--db", str(self.path)]
        inputs = ["orion", "fixture-run", "summary", "--manifest", str(manifest_path)]
        resumed = subprocess.run(command + ["reuse-result"] + inputs,
                                 check=True, capture_output=True, text=True)
        saved = json.loads(resumed.stdout)
        self.assertEqual(saved["result_text"], "Saved")
        replayed = subprocess.run(command + ["save-result"] + inputs + ["--result", "Saved"],
                                  check=True, capture_output=True, text=True)
        self.assertEqual(json.loads(replayed.stdout), saved)
        self.manifest["question"] = "Changed"
        manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        conflict = subprocess.run(command + ["reuse-result"] + inputs, capture_output=True, text=True)
        self.assertEqual(conflict.returncode, 2)
        self.assertIn("different inputs", conflict.stderr)
        self.assertEqual(conflict.stdout, "")
        self.assertEqual(MemoryBank(self.path).export("orion")["results"], [saved])

    def test_cli_rejects_ambiguous_and_oversized_json(self):
        manifest_path = Path(self.directory.name) / "inputs.json"
        command = [sys.executable, "-m", "lumina_orion.memory", "--db", str(self.path),
                   "save-result", "orion", "fixture-run", "summary", "--manifest", str(manifest_path),
                   "--result", "Summary"]
        for content in ('{"version":1,"version":2}', "[" * 1100, " " * 100001, "{broken"):
            manifest_path.write_text(content, encoding="utf-8")
            response = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(response.returncode, 2)
            self.assertIn("Memory journal error:", response.stderr)
        self.assertEqual(self.bank.export("orion")["results"], [])

    def test_progress_is_scoped_metadata_and_preserves_originals(self):
        self.bank.add("orion", "question", "Kept original", "fixture:original")
        last = self.bank.save_result("orion", "fixture-run", "z-last", self.manifest, "Last output")
        first = self.bank.save_result("orion", "fixture-run", "a-first", self.manifest, "First output")
        self.bank.save_result("orion", "other-run", "other", self.manifest, "Another run")
        self.bank.save_result("aurora", "fixture-run", "other", self.manifest, "Another profile")
        before = {profile: self.bank.export(profile) for profile in ("orion", "aurora")}
        bank = MemoryBank(self.path)
        expected = [{key: row[key] for key in ("step", "manifest_sha256", "created_at")}
                    for row in (first, last)]
        self.assertEqual(bank.result_progress(" ORION ", "fixture-run"),
                         {"profile": "orion", "run_id": "fixture-run", "saved_steps": expected})
        for profile, run in (("orion", "missing"), ("theo", "fixture-run")):
            self.assertEqual(bank.result_progress(profile, run)["saved_steps"], [])
        self.assertEqual([r["step"] for r in bank.result_progress("aurora", "fixture-run")["saved_steps"]],
                         ["other"])
        self.assertEqual({profile: bank.export(profile) for profile in before}, before)

    def test_progress_rejects_invalid_identity(self):
        before = self.bank.export("orion")
        for profile, run in (("unknown", "run"), ("orion", ""), ("orion", " "),
                             ("orion", "x" * 201), ("orion", True), ("orion", None)):
            with self.subTest(profile=profile, run=run), self.assertRaises(ValueError):
                self.bank.result_progress(profile, run)
        self.assertEqual(self.bank.export("orion"), before)

    def test_progress_does_not_expose_another_connections_uncommitted_result(self):
        original = self.save()
        before = self.bank.result_progress("orion", "fixture-run")
        with sqlite3.connect(self.path) as writer:
            writer.execute("BEGIN IMMEDIATE")
            writer.execute("""INSERT INTO research_results
                SELECT profile,run_id,'pending',manifest_json,manifest_sha256,result_text,created_at
                FROM research_results WHERE profile='orion' AND run_id='fixture-run' AND step='summary'""")
            self.assertEqual(self.bank.result_progress("orion", "fixture-run"), before)
            writer.rollback()
        self.assertEqual(MemoryBank(self.path).result_progress("orion", "fixture-run"), before)
        self.assertEqual(self.bank.export("orion")["results"], [original])

    def test_cli_progress_and_resume_at_both_commit_boundaries(self):
        manifest_path = Path(self.directory.name) / "inputs.json"
        manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        # Exit after the inserted row is read back, before _connect commits,
        # or immediately after save_result returns. Both use the real save API.
        worker = """
import json, os, sys
from lumina_orion.memory import MemoryBank
path, manifest_path, boundary = sys.argv[1:]
manifest = json.loads(open(manifest_path, encoding='utf-8').read())
class InterruptedBank(MemoryBank):
    @staticmethod
    def _matching_result(db, inputs):
        row = MemoryBank._matching_result(db, inputs)
        if row is not None and boundary == 'before':
            os._exit(17)
        return row
InterruptedBank(path).save_result('orion', 'fixture-run', 'summary', manifest, 'Saved')
os._exit(18)
"""
        for mode in ("DELETE", "WAL"):
            for boundary in ("before", "after"):
                with self.subTest(mode=mode, boundary=boundary):
                    path = Path(self.directory.name) / f"{mode}-{boundary}.sqlite3"
                    bank = MemoryBank(path)
                    with sqlite3.connect(path) as db:
                        self.assertEqual(db.execute(f"PRAGMA journal_mode={mode}").fetchone()[0], mode.lower())
                    bank.add("orion", "question", "Original", "fixture:kept")
                    original_notes = bank.export("orion")["records"]
                    prior = bank.save_result("orion", "fixture-run", "prior", self.manifest, "Earlier result")
                    bank.save_result("aurora", "fixture-run", "summary", self.manifest, "Unrelated")
                    other_before = bank.export("aurora")
                    exited = subprocess.run([sys.executable, "-c", worker, str(path),
                                             str(manifest_path), boundary],
                                            capture_output=True, text=True, timeout=10)
                    self.assertEqual(exited.returncode, 17 if boundary == "before" else 18, exited.stderr)
                    command = [sys.executable, "-m", "lumina_orion.memory", "--db", str(path)]
                    response = subprocess.run(command + ["progress", "orion", "fixture-run"],
                                              check=True, capture_output=True, text=True, timeout=10)
                    progress = json.loads(response.stdout)
                    self.assertEqual([r["step"] for r in progress["saved_steps"]],
                                     ["prior"] if boundary == "before" else ["prior", "summary"])
                    bank = MemoryBank(path)
                    saved = bank.reuse_result("orion", "fixture-run", "summary", self.manifest)
                    if boundary == "before":
                        self.assertIsNone(saved)
                    else:
                        self.assertEqual(saved["result_text"], "Saved")
                    resumed = bank.save_result("orion", "fixture-run", "summary", self.manifest, "Saved")
                    self.assertEqual(bank.save_result("orion", "fixture-run", "summary", self.manifest, "Saved"), resumed)
                    if saved is not None:
                        self.assertEqual(resumed, saved)
                    changed = {**self.manifest, "question": "Changed inputs"}
                    with self.assertRaisesRegex(ValueError, "different inputs"):
                        bank.reuse_result("orion", "fixture-run", "summary", changed)
                    self.assertEqual([r["step"] for r in bank.result_progress("orion", "fixture-run")["saved_steps"]],
                                     ["prior", "summary"])
                    self.assertEqual(bank.reuse_result("orion", "fixture-run", "prior", self.manifest), prior)
                    self.assertEqual(len(bank.export("orion")["results"]), 2)
                    self.assertEqual(bank.export("orion")["records"], original_notes)
                    self.assertEqual(bank.export("aurora"), other_before)


if __name__ == "__main__":
    unittest.main()

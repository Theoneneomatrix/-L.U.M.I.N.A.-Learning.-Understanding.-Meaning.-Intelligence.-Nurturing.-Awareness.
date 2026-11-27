import contextlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lumina_orion.capture import capture_journal
from lumina_orion.capture_export import ERROR, SIZE_ERROR, MAX_OUTPUT_BYTES, main
from lumina_orion.capture_inspect import MAX_INPUT_BYTES, _read
from lumina_orion.memory import MemoryBank, PROFILES
from lumina_orion.removal import RemovalIntentLedger
from test_capture_consistency import read_all
from test_capture_inspect import files, run as inspect_run
from test_capture_verification import verify
from test_note_binding import seed


def run(args, *, text=True):
    script = '''import runpy,socket,sys
def denied(*a,**k): raise AssertionError("network disabled")
socket.socket.connect=denied
socket.create_connection=denied
sys.modules["openai"]=None
sys.argv=["capture_export",*sys.argv[1:]]
runpy.run_module("lumina_orion.capture_export",run_name="__main__")
'''
    return subprocess.run([sys.executable, "-c", script, *args], text=text,
                          capture_output=True, timeout=15,
                          env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))


class CaptureExportTests(unittest.TestCase):
    def failure(self, path, profile="orion"):
        result = run(["--db", str(path), "--profile", profile])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, ERROR + "\n")

    def test_all_profiles_modes_restarts_and_inspection_preserve_originals(self):
        with tempfile.TemporaryDirectory() as directory:
            for mode in ("DELETE", "WAL"):
                path = Path(directory)/(mode + " #?mode=rw é.sqlite3")
                seed(self, path, mode)
                before = read_all(path); original = path.read_bytes()
                for profile in PROFILES:
                    baseline = capture_journal(path, profile)
                    # Retain expected envelopes before invoking the new command.
                    paths, inspect_args, expected = files(directory, baseline, profile=profile)
                    retained = {p: p.read_bytes() for k, p in paths.items() if k != "capture"}
                    outputs = []
                    for _ in range(2):
                        child = run(["--db", str(path), "--profile", " " + profile.upper() + " "])
                        self.assertEqual(child.returncode, 0, child.stderr)
                        self.assertEqual(child.stderr, "")
                        exported = json.loads(child.stdout)
                        self.assertEqual(exported, baseline)
                        self.assertEqual(exported["coverage"], "partial")
                        self.assertEqual(exported["authority"], "not_assessed")
                        self.assertEqual(exported["freshness"], "not_assessed")
                        self.assertEqual([r["review_status"] for r in exported["memory_export"]["records"]],
                                         ["superseded", "needs_review", "unverified"])
                        for row in (exported["memory_export"]["records"] +
                                    exported["memory_export"]["results"] + exported["saved_intents"]):
                            self.assertEqual(row["profile"], profile)
                        paths["capture"].write_text(child.stdout, encoding="utf-8")
                        inspected = inspect_run(inspect_args)
                        self.assertEqual(inspected.returncode, 0, inspected.stderr)
                        self.assertEqual(json.loads(inspected.stdout), verify(baseline, expected, profile=profile))
                        outputs.append(child.stdout)
                    self.assertEqual(outputs[0], outputs[1])
                    self.assertEqual({p: p.read_bytes() for p in retained}, retained)
                self.assertEqual(read_all(path), before)
                self.assertEqual(path.read_bytes(), original)

    def test_missing_paths_invalid_profiles_do_not_initialize(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for path in (root/"missing.sqlite3", root/"missing"/"journal.sqlite3"):
                self.failure(path)
                for profile in ("", "unknown", "theo\n"):
                    self.failure(path, profile)
            self.assertEqual(list(root.iterdir()), [])

    def test_incomplete_and_corrupt_stores_fail_without_an_empty_export(self):
        with tempfile.TemporaryDirectory() as directory:
            for missing in ("memories", "dependencies", "research_results", "removal_intents"):
                path = Path(directory)/(missing + ".sqlite3")
                seed(self, path, "DELETE")
                with sqlite3.connect(path) as db:
                    db.execute("DROP TABLE " + missing)  # Disposable fault fixture only.
                original = path.read_bytes()
                self.failure(path)
                self.assertEqual(path.read_bytes(), original)
            path = Path(directory)/"private-payload.sqlite3"
            path.write_bytes(b"Invented corrupt store; not SQLite")
            original = path.read_bytes(); self.failure(path)
            self.assertEqual(path.read_bytes(), original)
            self.failure(directory)

    def test_empty_existing_store_is_partial_not_proof_of_no_other_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"empty.sqlite3"
            MemoryBank(path); RemovalIntentLedger(path)
            original = path.read_bytes()
            child = run(["--db", str(path), "--profile", "theo"])
            self.assertEqual(child.returncode, 0, child.stderr)
            data = json.loads(child.stdout)
            self.assertEqual(data["memory_export"]["records"], [])
            self.assertEqual(data["memory_export"]["results"], [])
            self.assertEqual(data["saved_intents"], [])
            self.assertEqual(data["coverage"], "partial")
            self.assertEqual(path.read_bytes(), original)

    def test_required_paths_and_unsupported_actions_have_no_default(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"missing.sqlite3"
            args = ["--db", str(path), "--profile", "orion"]
            for bad in ([], args[:2], args[2:], args + ["--apply"], args + ["--register"],
                        args + ["--cleanup"], args + ["--output", str(path)],
                        ["--d", *args[1:]]):
                child = run(bad)
                self.assertEqual(child.returncode, 2)
                self.assertEqual(child.stdout, "")
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_no_constructor_or_sql_writes_and_no_partial_output_on_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"private-path.sqlite3"; seed(self, path, "DELETE")
            original = path.read_bytes(); before = read_all(path)
            args = ["--db", str(path), "--profile", "orion"]
            out, err = io.TextIOWrapper(io.BytesIO(), encoding="utf-8"), io.StringIO()
            with patch.object(MemoryBank, "__init__", side_effect=AssertionError("initialization")), \
                    patch.object(RemovalIntentLedger, "__init__", side_effect=AssertionError("initialization")), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(main(args), 0)
            self.assertEqual(json.loads(out.buffer.getvalue()), capture_journal(path, "orion"))
            self.assertEqual(err.getvalue(), "")
            def write(db, *args):
                db.execute("DELETE FROM memories")
            for target, effect in (("lumina_orion.capture.MemoryBank._records", write),
                                   ("lumina_orion.capture_export.json.dumps", ValueError("private-payload"))):
                out, err = io.StringIO(), io.StringIO()
                with patch(target, side_effect=effect), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    self.assertEqual(main(args), 2)
                self.assertEqual(out.getvalue(), "")
                self.assertEqual(err.getvalue(), ERROR + "\n")
            self.assertEqual(read_all(path), before)
            self.assertEqual(path.read_bytes(), original)

    def test_real_byte_boundaries_profiles_modes_and_restarts(self):
        self.assertEqual(MAX_OUTPUT_BYTES, MAX_INPUT_BYTES)
        with tempfile.TemporaryDirectory() as directory:
            for mode in ("DELETE", "WAL"):
                path = Path(directory)/(mode + ".sqlite3")
                seed(self, path, mode)
                bank = MemoryBank(path)
                # Build large stores through the public API, within each row limit.
                for profile in PROFILES:
                    for i in range(54):
                        bank.add(profile, "source_note", "x" * 19000, "fixture:bulk:" + str(i))
                    bank.add(profile, "source_note", "é😀\n", "fixture:unicode")
                    bank.add(profile, "source_note", "pad", "fixture:padding")
                for profile in PROFILES:
                    for delta in (-1, 0, 1):
                        baseline = capture_journal(path, profile)
                        wire = json.dumps(baseline, sort_keys=True, ensure_ascii=True, allow_nan=False).encode("utf-8") + b"\n"
                        old = next(r["text"] for r in baseline["memory_export"]["records"] if r["source"] == "fixture:padding")
                        padding = len(old) + MAX_OUTPUT_BYTES + delta - len(wire)
                        self.assertGreater(padding, 0)
                        self.assertLessEqual(padding, 20000)
                        # Adjust only a disposable fixture before its preservation snapshot.
                        with sqlite3.connect(path) as db:
                            db.execute("UPDATE memories SET text=? WHERE profile=? AND source=?",
                                       ("p" * padding, profile, "fixture:padding"))
                        baseline = capture_journal(path, profile)
                        expected = json.dumps(baseline, sort_keys=True, ensure_ascii=True, allow_nan=False).encode("utf-8") + b"\n"
                        self.assertEqual(len(expected), MAX_INPUT_BYTES + delta)
                        self.assertIn(b"\\u00e9\\ud83d\\ude00", expected)
                        original = path.read_bytes(); rows = read_all(path)
                        for _ in range(2):
                            child = run(["--db", str(path), "--profile", profile], text=False)
                            if delta <= 0:
                                self.assertEqual(child.returncode, 0, child.stderr)
                                self.assertEqual(child.stderr, b"")
                                self.assertEqual(child.stdout, expected)
                                exported = Path(directory)/"capture.json"
                                exported.write_bytes(child.stdout)
                                self.assertEqual(_read(exported), baseline)
                            else:
                                self.assertEqual(child.returncode, 2)
                                self.assertEqual(child.stdout, b"")
                                self.assertEqual(child.stderr, (SIZE_ERROR + "\n").encode("utf-8"))
                            self.assertEqual(read_all(path), rows)
                            self.assertEqual(path.read_bytes(), original)

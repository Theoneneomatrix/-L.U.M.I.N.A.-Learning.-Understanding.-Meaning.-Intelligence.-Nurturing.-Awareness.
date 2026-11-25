import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lumina_orion.capture import capture_journal
from lumina_orion.capture_inspect import ERROR, MAX_INPUT_BYTES, main
from lumina_orion.memory import PROFILES
from test_capture_consistency import read_all
from test_capture_verification import envelopes, value, verify
from test_note_binding import seed


def files(directory, data, identity="fixture:capture", profile="orion"):
    expected = envelopes(data, identity, profile)
    docs = dict(capture=data, notes=expected["expected_notes"],
                requests=expected["expected_requests"], results=expected["expected_results"])
    paths = {name: Path(directory)/(name + " é.json") for name in docs}
    for name, doc in docs.items():
        paths[name].write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    args = ["--profile", profile, "--capture-id", identity]
    for name, path in paths.items():
        args.extend(["--" + name, str(path)])
    return paths, args, expected


def run(args):
    # Exercise the actual module entry point in a fresh interpreter, with no
    # network or model package available and no bytecode writes to the fixture.
    script = '''import runpy,socket,sys
def denied(*a,**k): raise AssertionError("network disabled")
socket.socket.connect=denied
socket.create_connection=denied
sys.modules["openai"]=None
sys.argv=["capture_inspect",*sys.argv[1:]]
runpy.run_module("lumina_orion.capture_inspect",run_name="__main__")
'''
    return subprocess.run([sys.executable, "-c", script, *args], capture_output=True,
                          text=True, timeout=10,
                          env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))


class CaptureInspectTests(unittest.TestCase):
    def failure(self, args):
        result = run(args)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, ERROR + "\n")

    def test_module_prints_only_existing_partial_report_and_restarts(self):
        with tempfile.TemporaryDirectory() as directory:
            data = value(); paths, args, expected = files(directory, data)
            original = {p: p.read_bytes() for p in paths.values()}
            outputs = [run(args) for _ in range(2)]
            for result in outputs:
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")
                self.assertEqual(json.loads(result.stdout), verify(data, expected))
                self.assertNotIn("Invented", result.stdout)
            self.assertEqual(outputs[0].stdout, outputs[1].stdout)
            self.assertEqual({p: p.read_bytes() for p in paths.values()}, original)
            self.assertEqual(set(Path(directory).iterdir()), set(paths.values()))

    def test_duplicate_keys_at_every_file_and_nested_level_fail_before_report(self):
        with tempfile.TemporaryDirectory() as directory:
            paths, args, _ = files(directory, value())
            for name, path in paths.items():
                original = path.read_bytes()
                text = original.decode("utf-8")
                # Ordinary json.loads accepts these and discards the first key.
                duplicate = '{"version":999,' + text[1:]
                self.assertEqual(json.loads(duplicate), json.loads(text))
                path.write_text(duplicate, encoding="utf-8")
                self.failure(args)
                path.write_bytes(original)
            path = paths["capture"]; original = path.read_text(encoding="utf-8")
            # Escape-equivalent names are duplicate keys too, even in deep rows.
            nested = original.replace('"text": ', '"te\\u0078t":"discarded", "text": ', 1)
            self.assertEqual(json.loads(nested), json.loads(original))
            self.assertNotEqual(nested, original)
            path.write_text(nested, encoding="utf-8")
            self.failure(args)

    def test_mixed_valid_associations_foreign_profile_and_changed_rows_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            paths, args, _ = files(directory, value())
            for alternate in (envelopes(value(), "fixture:other"),
                              envelopes(value("aurora"), profile="aurora")):
                for name in ("notes", "requests", "results"):
                    original = paths[name].read_bytes()
                    paths[name].write_text(json.dumps(alternate["expected_" + name]), encoding="utf-8")
                    self.failure(args)
                    paths[name].write_bytes(original)
            changed = value(); changed["memory_export"]["results"][0]["result_text"] += " changed"
            paths["capture"].write_text(json.dumps(changed), encoding="utf-8")
            self.failure(args)

    def test_bad_json_unicode_numbers_roots_bounds_and_missing_paths(self):
        bad = [b"", b"{", b"{} {}", b"\xff", b"\xef\xbb\xbf{}", b"[]", b"null",
               b'{"x":NaN}', b'{"x":Infinity}', b'{"x":-Infinity}', b'{"x":1e999}',
               b'{"x":' + b'['*2000 + b'0' + b']'*2000 + b'}',
               b' '*(MAX_INPUT_BYTES+1)]
        with tempfile.TemporaryDirectory() as directory:
            paths, args, _ = files(directory, value())
            for content in bad:
                paths["capture"].write_bytes(content)
                self.failure(args)
            paths["capture"].unlink()
            self.failure(args)
            paths["capture"].mkdir()
            self.failure(args)
            paths["capture"].rmdir()
            # The byte ceiling is inclusive, including harmless whitespace.
            raw = json.dumps(value()).encode()
            paths["capture"].write_bytes(raw + b' '*(MAX_INPUT_BYTES-len(raw)))
            self.assertEqual(run(args).returncode, 0)
            paths["capture"].write_bytes(raw + b' '*(MAX_INPUT_BYTES-len(raw)+1))
            self.failure(args)

    def test_required_arguments_and_unknown_actions_are_not_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            _, args, _ = files(directory, value())
            for bad in ([], args[:-2], args + ["--apply"], args + ["--register"],
                        args + ["--cleanup"], ["--prof", *args[1:]]):
                result = run(bad)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")

    def test_all_profiles_modes_fresh_processes_preserve_database_and_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            for mode in ("DELETE", "WAL"):
                db = Path(directory)/(mode + ".sqlite3"); seed(self, db, mode)
                original = db.read_bytes(); rows = read_all(db)
                for profile in PROFILES:
                    data = capture_journal(db, profile)
                    paths, args, expected = files(directory, data, profile=profile)
                    inputs = {p: p.read_bytes() for p in paths.values()}
                    for _ in range(2):
                        result = run(args)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(json.loads(result.stdout), verify(data, expected, profile=profile))
                    wrong = args.copy(); wrong[1] = "aurora" if profile == "orion" else "orion"
                    self.failure(wrong)
                    self.assertEqual({p: p.read_bytes() for p in paths.values()}, inputs)
                self.assertEqual(read_all(db), rows)
                self.assertEqual(db.read_bytes(), original)

    def test_main_can_return_failure_without_echoing_path_key_or_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"private-label.json"
            path.write_text('{"private-key":"private-payload","private-key":0}', encoding="utf-8")
            args = ["--profile", "orion", "--capture-id", "private-capture"]
            for name in ("capture", "notes", "requests", "results"):
                args.extend(["--" + name, str(path)])
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(main(args), 2)
            self.assertEqual(out.getvalue(), "")
            self.assertEqual(err.getvalue(), ERROR + "\n")

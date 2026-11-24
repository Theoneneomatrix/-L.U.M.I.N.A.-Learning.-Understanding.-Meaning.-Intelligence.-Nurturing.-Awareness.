import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lumina_orion.capture import capture_journal
from lumina_orion.capture_binding import bind_capture_notes
from lumina_orion.capture_verification import verify_capture_bindings
from lumina_orion.memory import PROFILES, _manifest_json
from lumina_orion.request_binding import bind_capture_requests
from lumina_orion.result_binding import bind_capture_results
from test_capture_binding import capture
from test_capture_consistency import read_all
from test_note_binding import seed
from test_request_binding import request
from test_result_binding import result


def value(profile="orion"):
    data = capture(profile)
    data["saved_intents"] = [request(profile)]
    data["memory_export"]["results"] = [result(profile)]
    return data


def envelopes(data, identity="fixture:capture", profile="orion"):
    return dict(expected_notes=bind_capture_notes(profile, data, capture_id=identity),
                expected_requests=bind_capture_requests(profile, data, capture_id=identity),
                expected_results=bind_capture_results(profile, data, capture_id=identity))


def verify(data, expected, identity="fixture:capture", profile="orion"):
    return verify_capture_bindings(profile, data, capture_id=identity, **expected)


class CaptureVerificationTests(unittest.TestCase):
    def test_exact_report_empty_sets_and_no_payload_or_authority(self):
        data = value(); expected = envelopes(data)
        report = verify(data, expected, " fixture:capture ", " ORION ")
        self.assertEqual(report, dict(
            version=1, scheme="lumina-capture-binding-report-v1", profile="orion",
            capture_id="fixture:capture", status="supplied_bindings_match",
            scope="supplied_note_request_result_rows", coverage="partial",
            common_snapshot="not_proven", authenticity="not_assessed",
            freshness="not_assessed", authority="not_assessed",
            counts=dict(notes=1, requests=1, results=1),
            bindings=dict(notes_sha256=expected["expected_notes"]["notes_sha256"],
                          requests_sha256=expected["expected_requests"]["requests_sha256"],
                          results_sha256=expected["expected_results"]["results_sha256"])))
        for text in ("Invented", "requested", "fixture:source"):
            self.assertNotIn(text, json.dumps(report))
        empty = capture(); empty["memory_export"]["records"] = []
        self.assertEqual(verify(empty, envelopes(empty))["counts"],
                         dict(notes=0, requests=0, results=0))
        self.assertEqual(verify(empty, envelopes(empty))["coverage"], "partial")

    def test_individually_valid_mixed_capture_and_profile_envelopes_rejected(self):
        data = value(); expected = envelopes(data)
        for alternate in (envelopes(data, "fixture:other"),
                          envelopes(value("aurora"), profile="aurora")):
            for key in expected:
                mixed = copy.deepcopy(expected); mixed[key] = alternate[key]
                before = copy.deepcopy((data, mixed))
                with self.subTest(key=key), self.assertRaises(ValueError): verify(data, mixed)
                self.assertEqual((data, mixed), before)
        with self.assertRaises(ValueError): verify(data, expected, "fixture:other")
        for p in PROFILES:
            own = value(p); report = verify(own, envelopes(own, profile=p), profile=p)
            self.assertEqual(report["profile"], p)
            if p != "orion":
                with self.assertRaises(ValueError): verify(own, expected, profile=p)

    def test_each_payload_conflict_malformed_or_missing_envelope_fails(self):
        data = value(); expected = envelopes(data)
        for section, field in (("records", "text"), ("results", "result_text"),
                               ("requests", "target_key")):
            changed = copy.deepcopy(data)
            rows = changed["saved_intents"] if section == "requests" else changed["memory_export"][section]
            rows[0][field] += " changed"
            with self.assertRaises(ValueError): verify(changed, expected)
        for key in expected:
            for bad in (None, {}, dict(expected[key], version=True),
                        dict(expected[key], extra="private")):
                with self.assertRaises(ValueError) as error: verify(data, dict(expected, **{key: bad}))
                self.assertNotIn("private", str(error.exception))
        for identity in (None, "", "x"*201, "bad\nlabel", "\ud800"):
            with self.assertRaises(ValueError): verify(data, expected, identity)
        with self.assertRaises(ValueError): verify(dict(data, authority="trusted"), expected)

    def test_inputs_unchanged_and_report_detached(self):
        data = value(); expected = envelopes(data); before = copy.deepcopy((data, expected))
        report = verify(data, expected)
        report["counts"]["notes"] = 999
        report["bindings"]["notes_sha256"] = "changed"
        self.assertEqual((data, expected), before)
        self.assertEqual(verify(data, expected)["counts"]["notes"], 1)

    def test_relabelled_assembled_and_replaced_inputs_are_not_provenance(self):
        data = value(); expected = envelopes(data)
        assembled = copy.deepcopy(data)
        assembled["memory_export"]["results"][0]["result_text"] = "fixture:other snapshot"
        mixed = dict(expected, expected_results=envelopes(assembled)["expected_results"])
        with self.assertRaises(ValueError): verify(data, mixed)
        # Same declared label for separately assembled contents still matches when
        # all expected bindings are constructed from those contents.
        report = verify(assembled, envelopes(assembled))
        self.assertEqual(report["common_snapshot"], "not_proven")
        self.assertEqual(report["authenticity"], "not_assessed")
        for rows in (assembled["memory_export"]["records"],
                     assembled["memory_export"]["results"], assembled["saved_intents"]):
            rows.clear()
        report = verify(assembled, envelopes(assembled))
        self.assertEqual(report["counts"], dict(notes=0, requests=0, results=0))
        self.assertEqual(report["coverage"], "partial")

    def test_all_profiles_fresh_processes_preserve_originals_and_digests(self):
        script = '''import json,sys
from lumina_orion.capture import capture_journal
from lumina_orion.capture_verification import verify_capture_bindings
print(json.dumps(verify_capture_bindings(sys.argv[2],capture_journal(sys.argv[1],sys.argv[2]),capture_id="fixture:restart",**json.loads(sys.stdin.read()))))
'''
        with tempfile.TemporaryDirectory() as directory:
            for mode in ("DELETE", "WAL"):
                path = Path(directory)/(mode+".sqlite3"); seed(self, path, mode)
                before = read_all(path); original = path.read_bytes()
                for p in PROFILES:
                    data = capture_journal(path, p)
                    expected = envelopes(data, "fixture:restart", p)
                    report = verify(data, expected, "fixture:restart", p)
                    for _ in range(2):
                        child = subprocess.run([sys.executable, "-c", script, str(path), p],
                                               input=json.dumps(expected), text=True,
                                               capture_output=True, check=True, timeout=10)
                        self.assertEqual(json.loads(child.stdout), report)
                    row = data["memory_export"]["results"][0]
                    self.assertEqual(row["manifest_sha256"], hashlib.sha256(
                        _manifest_json(row["manifest"]).encode()).hexdigest())
                    self.assertEqual(data["saved_intents"][0]["content_sha256"], hashlib.sha256(
                        data["memory_export"]["records"][0]["text"].encode()).hexdigest())
                self.assertEqual(read_all(path), before)
                self.assertEqual(path.read_bytes(), original)

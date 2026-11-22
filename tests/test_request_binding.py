import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lumina_orion.capture import capture_journal
from lumina_orion.memory import PROFILES
from lumina_orion.request_binding import bind_capture_requests, require_capture_request_binding
from test_capture_binding import capture
from test_capture_consistency import read_all
from test_note_binding import seed


def request(profile="orion"):
    return dict(id=1, version=1, profile=profile, request_id="fixture:request",
                target_key="fixture:é😀", content_sha256="a"*64,
                created_at="fixture:time ", state="requested")


def value(profile="orion"):
    result = capture(profile)
    result["saved_intents"] = [request(profile)]
    return result


def bind(data, identity="fixture:capture", profile="orion"):
    return bind_capture_requests(profile, data, capture_id=identity)


def require(data, expected, identity="fixture:capture", profile="orion"):
    return require_capture_request_binding(profile, data, expected, capture_id=identity)


class CaptureRequestBindingTests(unittest.TestCase):
    def test_literal_empty_vector_and_order(self):
        data = capture()
        encoded = b'{"capture_id":"fixture:empty","profile":"orion","requests":[],"scheme":"lumina-capture-requests-sha256-v1","version":1}'
        expected = dict(version=1, scheme="lumina-capture-requests-sha256-v1",
                        profile="orion", capture_id="fixture:empty", requests=[],
                        requests_sha256=hashlib.sha256(
                            b"lumina-capture-requests-sha256-v1\x00"+encoded).hexdigest())
        self.assertEqual(bind(data, " fixture:empty "), expected)
        self.assertEqual(require(data, expected, "fixture:empty"), [])
        data = value()
        data["saved_intents"].append(dict(request(), id=2, request_id="fixture:second"))
        expected = bind(data)
        data["saved_intents"].reverse()
        self.assertEqual(bind(data), expected)
        self.assertEqual([r["id"] for r in require(data, expected)], [1, 2])
        with self.assertRaises(ValueError): require(data, expected, "fixture:other")

    def test_each_field_missing_extra_duplicate_and_foreign_conflicts(self):
        data = value(); expected = bind(data)
        changes = dict(id=2, version=2, profile="aurora", request_id="fixture:other",
                       target_key="fixture:e\u0301😀", content_sha256="b"*64,
                       created_at="fixture:time", state="completed")
        variants = []
        for field, replacement in changes.items():
            x = value(); x["saved_intents"][0][field] = replacement; variants.append(x)
        x = value(); x["saved_intents"] = []; variants.append(x)
        x = value(); x["saved_intents"].append(dict(request(), id=2, request_id="fixture:second")); variants.append(x)
        x = value(); x["saved_intents"] *= 2; variants.append(x)
        x = value(); x["saved_intents"].append(dict(request(), id=2)); variants.append(x)
        x = value(); x["saved_intents"].append(dict(request(), request_id="fixture:second")); variants.append(x)
        for x in variants:
            before = copy.deepcopy(x)
            with self.assertRaises(ValueError): require(x, expected)
            self.assertEqual(x, before)
        digests = {bind(value(p), profile=p)["requests_sha256"] for p in PROFILES}
        self.assertEqual(len(digests), len(PROFILES))
        for p in PROFILES[1:]:
            with self.assertRaises(ValueError): require(value(p), expected, profile=p)

    def test_malformed_rows_envelopes_and_capture_fail_closed(self):
        rows = [None, {}, dict(request(), extra="private")]
        for field in request():
            x = request(); del x[field]; rows.append(x)
        for field, replacement in [("id", True), ("id", 2**63), ("version", True),
                                    ("request_id", " padded "), ("target_key", "x"*2001),
                                    ("target_key", "bad\nkey"), ("created_at", ""),
                                    ("created_at", "\ud800"), ("content_sha256", "A"*64)]:
            rows.append(dict(request(), **{field: replacement}))
        for row in rows:
            x = value(); x["saved_intents"] = [row]
            with self.assertRaises(ValueError) as error: bind(x)
            self.assertNotIn("private", str(error.exception))
        expected = bind(value())
        bad = [None, {}, dict(expected, extra=True)]
        for field in expected:
            x = copy.deepcopy(expected); del x[field]; bad.append(x)
        for field, replacement in [("version", True), ("scheme", "other"),
                                   ("profile", "aurora"), ("capture_id", "\ud800"),
                                   ("requests", None), ("requests_sha256", "A"*64)]:
            bad.append(dict(expected, **{field: replacement}))
        x = copy.deepcopy(expected); x["requests"][0]["created_at"] = "forged"; bad.append(x)
        for x in bad:
            with self.assertRaises(ValueError): require(value(), x)
        for field in value():
            x = value(); del x[field]
            with self.assertRaises(ValueError): bind(x)
        for field, replacement in [("version", True), ("profile", "aurora"),
                                   ("coverage", "complete"), ("freshness", "current"),
                                   ("authority", "trusted"), ("consistency", "assumed")]:
            with self.assertRaises(ValueError): bind(dict(value(), **{field: replacement}))
        for identity in (None, "", "x"*201, "bad\nidentity", "\ud800"):
            with self.assertRaises(ValueError): bind(value(), identity)
        for p in (None, "", "unknown", True):
            with self.assertRaises(ValueError): bind(value(), profile=p)

    def test_detachment_and_resource_bounds(self):
        data = value(); before = copy.deepcopy(data); expected = bind(data)
        expected["requests"][0]["target_key"] = "fixture:mutated"
        self.assertEqual(data, before)
        expected = bind(data); rows = require(data, expected)
        rows[0]["request_id"] = "fixture:mutated"
        self.assertEqual(bind(data), expected)
        self.assertEqual(data, before)
        with patch("lumina_orion.request_binding.MAX_ENVELOPE_BYTES", 1):
            with self.assertRaises(ValueError): bind(data)
        for field in ("records", "results"):
            x = value(); x["memory_export"][field] = [None]*10001
            with self.assertRaises(ValueError): bind(x)
        x = value(); x["saved_intents"] = [None]*10001
        with self.assertRaises(ValueError): bind(x)

    def test_unbound_payload_replaced_expected_and_omission_negative_controls(self):
        data = value(); expected = bind(data)
        changed = copy.deepcopy(data)
        changed["memory_export"]["records"] = [{"fixture": "unbound note"}]
        changed["memory_export"]["results"] = [{"fixture": "unbound result"}]
        self.assertEqual(bind(changed), expected)
        self.assertEqual(require(changed, expected), data["saved_intents"])
        changed["saved_intents"][0]["target_key"] = "fixture:forged"
        with self.assertRaises(ValueError): require(changed, expected)
        self.assertEqual(require(changed, bind(changed)), changed["saved_intents"])
        changed["saved_intents"] = []
        self.assertEqual(require(changed, bind(changed)), [])

    def test_all_profiles_restart_originals_and_v1_text_digests(self):
        script = '''import json,sys
from lumina_orion.capture import capture_journal
from lumina_orion.request_binding import require_capture_request_binding
print(json.dumps(require_capture_request_binding(sys.argv[2],capture_journal(sys.argv[1],sys.argv[2]),json.loads(sys.stdin.read()),capture_id="fixture:restart")))
'''
        with tempfile.TemporaryDirectory() as directory:
            for mode in ("DELETE", "WAL"):
                path = Path(directory)/(mode+".sqlite3"); seed(self, path, mode)
                before = read_all(path); original = path.read_bytes()
                for profile in PROFILES:
                    data = capture_journal(path, profile)
                    expected = bind(data, "fixture:restart", profile)
                    for _ in range(2):
                        child = subprocess.run([sys.executable, "-c", script, str(path), profile],
                                               input=json.dumps(expected), capture_output=True,
                                               text=True, check=True, timeout=10)
                        self.assertEqual(json.loads(child.stdout), data["saved_intents"])
                    rows = data["memory_export"]["records"]
                    self.assertEqual(data["saved_intents"][0]["content_sha256"],
                                     hashlib.sha256(rows[0]["text"].encode()).hexdigest())
                    self.assertEqual(data, capture_journal(path, profile))
                self.assertEqual(read_all(path), before)
                self.assertEqual(path.read_bytes(), original)

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
from lumina_orion.memory import PROFILES, _manifest_json
from lumina_orion.result_binding import bind_capture_results, require_capture_result_binding
from test_capture_binding import capture
from test_capture_consistency import read_all
from test_note_binding import seed


def result(profile="orion"):
    manifest = dict(version=1, question="Invented question", sources=[
        dict(locator="fixture:source", content_sha256="a"*64)],
        processing=dict(pipeline="fixture:v1", options=[True, None, 1.5]))
    return dict(profile=profile, run_id="fixture:run", step="fixture:step",
                manifest=manifest, manifest_sha256=hashlib.sha256(
                    _manifest_json(manifest).encode()).hexdigest(),
                result_text="Invented é😀\n", created_at="fixture:time ")


def value(profile="orion"):
    data = capture(profile)
    data["memory_export"]["results"] = [result(profile)]
    return data


def bind(data, identity="fixture:capture", profile="orion"):
    return bind_capture_results(profile, data, capture_id=identity)


def require(data, expected, identity="fixture:capture", profile="orion"):
    return require_capture_result_binding(profile, data, expected, capture_id=identity)


class CaptureResultBindingTests(unittest.TestCase):
    def test_literal_empty_vector_and_order(self):
        encoded = b'{"capture_id":"fixture:empty","profile":"orion","results":[],"scheme":"lumina-capture-results-sha256-v1","version":1}'
        expected = dict(version=1, scheme="lumina-capture-results-sha256-v1",
                        profile="orion", capture_id="fixture:empty", results=[],
                        results_sha256=hashlib.sha256(
                            b"lumina-capture-results-sha256-v1\x00"+encoded).hexdigest())
        self.assertEqual(bind(capture(), " fixture:empty "), expected)
        self.assertEqual(require(capture(), expected, "fixture:empty"), [])
        data = value()
        data["memory_export"]["results"] += [dict(result(), step="a"),
                                               dict(result(), run_id="a")]
        expected = bind(data)
        data["memory_export"]["results"].reverse()
        for row in data["memory_export"]["results"]:
            row["manifest"] = dict(reversed(list(row["manifest"].items())))
        self.assertEqual(bind(data), expected)
        self.assertEqual([(r["run_id"], r["step"]) for r in require(data, expected)],
                         [("a", "fixture:step"), ("fixture:run", "a"),
                          ("fixture:run", "fixture:step")])
        with self.assertRaises(ValueError): require(data, expected, "fixture:other")
        bad = copy.deepcopy(expected); bad["results"].reverse()
        with self.assertRaises(ValueError): require(data, bad)

    def test_each_field_nested_manifest_and_exact_unicode_conflicts(self):
        expected = bind(value())
        changes = dict(profile="aurora", run_id="other", step="other",
                       manifest_sha256="b"*64, result_text="Invented e\u0301😀\n",
                       created_at="fixture:time")
        for field, replacement in changes.items():
            data = value(); data["memory_export"]["results"][0][field] = replacement
            before = copy.deepcopy(data)
            with self.subTest(field=field), self.assertRaises(ValueError): require(data, expected)
            self.assertEqual(data, before)
        for change in ("question", "sources", "processing"):
            data = value(); row = data["memory_export"]["results"][0]
            if change == "question": row["manifest"][change] += " changed"
            elif change == "sources": row["manifest"][change][0]["locator"] = "fixture:other"
            else: row["manifest"][change]["options"].reverse()
            # Even a valid, recomputed input digest cannot replace the retained result.
            row["manifest_sha256"] = hashlib.sha256(_manifest_json(row["manifest"]).encode()).hexdigest()
            with self.assertRaises(ValueError): require(data, expected)
        data = value(); data["memory_export"]["results"][0]["result_text"] = "Invented é😀"
        with self.assertRaises(ValueError): require(data, expected)

    def test_missing_extra_duplicate_and_profile_separation(self):
        expected = bind(value())
        for rows in ([], [result(), dict(result(), step="another")], [result(), result()]):
            data = value(); data["memory_export"]["results"] = rows
            with self.assertRaises(ValueError): require(data, expected)
        digests = {bind(value(p), profile=p)["results_sha256"] for p in PROFILES}
        self.assertEqual(len(digests), len(PROFILES))
        for p in PROFILES[1:]:
            with self.assertRaises(ValueError): require(value(p), expected, profile=p)

    def test_malformed_rows_manifests_and_bounds(self):
        bad = [None, {}, dict(result(), extra="private")]
        for field in result():
            row = result(); del row[field]; bad.append(row)
        for field, replacement in [("run_id", ""), ("step", True), ("run_id", "x"*201),
                                    ("result_text", "x"*20001), ("created_at", ""),
                                    ("created_at", "\ud800"), ("manifest_sha256", "A"*64),
                                    ("manifest", None)]:
            bad.append(dict(result(), **{field: replacement}))
        for field, replacement in [("version", True), ("question", "\ud800"),
                                    ("sources", []), ("processing", {"pipeline": "x", "x": float("nan")})]:
            row = result(); row["manifest"][field] = replacement; bad.append(row)
        row = result(); row["manifest"]["processing"]["loop"] = row["manifest"]; bad.append(row)
        for row in bad:
            data = value(); data["memory_export"]["results"] = [row]
            with self.assertRaises(ValueError) as error: bind(data)
            self.assertNotIn("private", str(error.exception))
        with patch("lumina_orion.result_binding.MAX_ENVELOPE_BYTES", 1):
            with self.assertRaises(ValueError): bind(value())
        # Actual aggregate bound, not just a patched constant.
        data = value(); data["memory_export"]["results"] = [
            dict(result(), step=str(i), result_text="😀"*20000) for i in range(5)]
        with self.assertRaises(ValueError): bind(data)
        for field in ("records", "results"):
            data = value(); data["memory_export"][field] = [None]*10001
            with self.assertRaises(ValueError): bind(data)

    def test_malformed_expected_and_capture_fail_closed(self):
        expected = bind(value())
        bad = [None, {}, dict(expected, extra=True)]
        for field in expected:
            x = copy.deepcopy(expected); del x[field]; bad.append(x)
        for field, replacement in [("version", True), ("scheme", "other"),
                                    ("profile", "aurora"), ("capture_id", " padded "),
                                    ("results", None), ("results_sha256", "A"*64)]:
            bad.append(dict(expected, **{field: replacement}))
        x = copy.deepcopy(expected); x["results"][0]["result_text"] = "forged"; bad.append(x)
        for x in bad:
            with self.assertRaises(ValueError): require(value(), x)
        for field in value():
            data = value(); del data[field]
            with self.assertRaises(ValueError): bind(data)
        for field, replacement in [("version", True), ("profile", "aurora"),
                                    ("coverage", "complete"), ("freshness", "current"),
                                    ("authority", "trusted"), ("consistency", "assumed")]:
            with self.assertRaises(ValueError): bind(dict(value(), **{field: replacement}))
        for identity in (None, "", "x"*201, "bad\nidentity", "\ud800"):
            with self.assertRaises(ValueError): bind(value(), identity)
        for profile in (None, "", True, "unknown"):
            with self.assertRaises(ValueError): bind(value(), profile=profile)

    def test_detachment_and_unbound_payload_trust_negative_controls(self):
        data = value(); original = copy.deepcopy(data); expected = bind(data)
        expected["results"][0]["manifest"]["processing"]["options"].append("detached")
        self.assertEqual(data, original)
        expected = bind(data); rows = require(data, expected)
        rows[0]["manifest"]["sources"][0]["locator"] = "detached"
        self.assertEqual(bind(data), expected)
        self.assertEqual(data, original)
        data["memory_export"]["records"] = [{"fixture": "unbound note"}]
        data["saved_intents"] = [{"fixture": "unbound request"}]
        self.assertEqual(require(data, expected), original["memory_export"]["results"])
        data["memory_export"]["results"][0]["result_text"] = "forged"
        with self.assertRaises(ValueError): require(data, expected)
        self.assertEqual(require(data, bind(data)), data["memory_export"]["results"])
        data["memory_export"]["results"] = []
        self.assertEqual(require(data, bind(data)), [])

    def test_all_profiles_restart_originals_and_unchanged_input_and_text_digests(self):
        script = '''import json,sys
from lumina_orion.capture import capture_journal
from lumina_orion.result_binding import require_capture_result_binding
print(json.dumps(require_capture_result_binding(sys.argv[2],capture_journal(sys.argv[1],sys.argv[2]),json.loads(sys.stdin.read()),capture_id="fixture:restart")))
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
                        self.assertEqual(json.loads(child.stdout), data["memory_export"]["results"])
                    row = expected["results"][0]
                    self.assertEqual(row["manifest_sha256"], hashlib.sha256(
                        _manifest_json(row["manifest"]).encode()).hexdigest())
                    self.assertEqual(data["saved_intents"][0]["content_sha256"], hashlib.sha256(
                        data["memory_export"]["records"][0]["text"].encode()).hexdigest())
                    self.assertEqual(data, capture_journal(path, profile))
                self.assertEqual(read_all(path), before)
                self.assertEqual(path.read_bytes(), original)

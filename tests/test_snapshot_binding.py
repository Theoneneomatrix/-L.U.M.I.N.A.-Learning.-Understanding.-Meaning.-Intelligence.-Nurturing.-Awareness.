import copy
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lumina_orion.memory import MemoryBank
from lumina_orion.restore import inspect_restore
from lumina_orion.snapshot_binding import (
    MAX_MANIFEST_BYTES, bind_snapshot_manifest, require_snapshot_binding,
)


def fixture(profile="orion"):
    return {"version": 1, "profile": profile, "snapshot_id": "fixture:A",
            "removal_history_boundary": None,
            "coverage": {"scope": "profile_records", "declaration": "partial",
                         "exclusions": []}, "records": []}


def encoded(value):
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


class SnapshotBindingTests(unittest.TestCase):
    def test_fixed_vector_and_equivalent_json_representations(self):
        value = fixture()
        binding = bind_snapshot_manifest("orion", encoded(value))
        self.assertEqual(binding, {
            "version": 1, "scheme": "lumina-manifest-sha256-v1", "profile": "orion",
            "snapshot_id": "fixture:A", "manifest_sha256":
            "be972fa2bb2460c0a7a53285a6e00d15463ed0f9bbd174bb2a1a50f9ee8da9f4"})
        reordered = dict(reversed(list(value.items())))
        reordered["coverage"] = dict(reversed(list(value["coverage"].items())))
        payload = (" \r\n" + json.dumps(reordered, indent=3) + "\t").encode()
        self.assertEqual(bind_snapshot_manifest(" ORION ", payload), binding)
        value["profile"] = " ORION "
        value["snapshot_id"] = " fixture:A "
        self.assertEqual(bind_snapshot_manifest("orion", encoded(value)), binding)

    def test_changed_declarations_conflict_even_under_same_snapshot_identity(self):
        value = fixture()
        expected = bind_snapshot_manifest("orion", encoded(value))
        changes = []
        for field, replacement in [("snapshot_id", "fixture:B"),
                                   ("removal_history_boundary", "fixture:old")]:
            changed = copy.deepcopy(value)
            changed[field] = replacement
            changes.append(changed)
        for field, replacement in [("declaration", "declared_complete"),
                                   ("exclusions", ["fixture:excluded"])]:
            changed = copy.deepcopy(value)
            changed["coverage"][field] = replacement
            changes.append(changed)
        record = {"profile": "orion", "record_key": "fixture:record",
                  "target_key": "fixture:target", "content_sha256": "a" * 64}
        populated = copy.deepcopy(value)
        populated["records"] = [record]
        changes.append(populated)
        for changed in changes:
            with self.subTest(changed=changed):
                with self.assertRaisesRegex(ValueError, "conflict"):
                    require_snapshot_binding("orion", encoded(changed), expected)
        expected = bind_snapshot_manifest("orion", encoded(populated))
        for field, replacement in [("record_key", "fixture:other"),
                                   ("target_key", "fixture:other"),
                                   ("content_sha256", "b" * 64)]:
            changed = copy.deepcopy(populated)
            changed["records"][0][field] = replacement
            with self.assertRaisesRegex(ValueError, "conflict"):
                require_snapshot_binding("orion", encoded(changed), expected)

    def test_array_order_and_unicode_sequences_are_explicitly_significant(self):
        value = fixture()
        value["snapshot_id"] = "fixture:é😀"
        payload = encoded(value)
        binding = bind_snapshot_manifest("orion", payload)
        self.assertEqual(bind_snapshot_manifest("orion", json.dumps(value).encode()), binding)
        value["snapshot_id"] = "fixture:e\u0301😀"
        self.assertNotEqual(bind_snapshot_manifest("orion", encoded(value)), binding)
        value["coverage"]["exclusions"] = ["fixture:x", "fixture:y"]
        binding = bind_snapshot_manifest("orion", encoded(value))
        value["coverage"]["exclusions"].reverse()
        self.assertNotEqual(bind_snapshot_manifest("orion", encoded(value)), binding)
        value["records"] = [{"profile": "orion", "record_key": key,
                             "target_key": "fixture:target", "content_sha256": "a" * 64}
                            for key in ("fixture:one", "fixture:two")]
        binding = bind_snapshot_manifest("orion", encoded(value))
        value["records"].reverse()
        self.assertNotEqual(bind_snapshot_manifest("orion", encoded(value)), binding)

    def test_duplicate_members_at_every_schema_level_fail_including_escaped_names(self):
        base = json.dumps(fixture())
        record = '{"profile":"orion","record_key":"x","target_key":"y","content_sha256":"' + 'a' * 64 + '"}'
        values = [base.replace('"version": 1', '"version": 1,"version":1'),
                  base.replace('"version": 1', '"version": 1,"ver\\u0073ion":1'),
                  base.replace('"scope": "profile_records"',
                               '"scope":"profile_records","scope":"profile_records"'),
                  base.replace('"records": []', '"records":[' + record.replace(
                      '"target_key":"y"', '"target_key":"x","target_key":"y"') + ']')]
        for payload in values:
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(ValueError, "ambiguous"):
                    bind_snapshot_manifest("orion", payload.encode())

    def test_invalid_encoding_numbers_shapes_and_resource_limits_fail(self):
        base = encoded(fixture())
        invalid = [b"", b"{} {}", b"\xff", b"\xef\xbb\xbf" + base,
                   base.decode().encode("utf-16"), b"[]", b"null",
                   b"[" * 2000 + b"]" * 2000, b" " * (MAX_MANIFEST_BYTES + 1),
                   base.replace(b'fixture:A', b'fixture:\\ud800')]
        for number in (b"NaN", b"Infinity", b"-Infinity", b"1.0", b"1e0", b"true", b"2"):
            invalid.append(base.replace(b'"version": 1', b'"version": ' + number))
        invalid.extend([base.decode(), bytearray(base), None])
        for payload in invalid:
            with self.subTest(payload_type=type(payload), length=len(payload) if payload else 0):
                with self.assertRaises(ValueError):
                    bind_snapshot_manifest("orion", payload)
        # Exactly the byte limit is allowed when the excess is JSON whitespace.
        padded = base + b" " * (MAX_MANIFEST_BYTES - len(base))
        self.assertEqual(bind_snapshot_manifest("orion", padded),
                         bind_snapshot_manifest("orion", base))

    def test_profile_separation_and_expected_binding_validation(self):
        for profile in ("orion", "aurora", "theo", "elysia", "selene"):
            payload = encoded(fixture(profile))
            binding = bind_snapshot_manifest(profile, payload)
            self.assertEqual(require_snapshot_binding(profile, payload, binding), fixture(profile))
            if profile != "orion":
                with self.assertRaises(ValueError):
                    bind_snapshot_manifest("orion", payload)
                with self.assertRaises(ValueError):
                    require_snapshot_binding("orion", encoded(fixture()), binding)
        expected = bind_snapshot_manifest("orion", encoded(fixture()))
        for field, bad in [("version", True), ("version", 2), ("scheme", "unknown"),
                           ("manifest_sha256", "A" * 64), ("snapshot_id", "other")]:
            changed = {**expected, field: bad}
            with self.assertRaises(ValueError):
                require_snapshot_binding("orion", encoded(fixture()), changed)
        for changed in ({**expected, "extra": 1}, {k: v for k, v in expected.items() if k != "scheme"}):
            with self.assertRaises(ValueError):
                require_snapshot_binding("orion", encoded(fixture()), changed)

    def test_match_is_detached_and_never_upgrades_restore_or_establishes_currentness(self):
        value = fixture()
        value["coverage"]["declaration"] = "declared_complete"
        value["removal_history_boundary"] = "fixture:claims-current"
        payload = encoded(value)
        binding = bind_snapshot_manifest("orion", payload)
        before = copy.deepcopy(binding)
        checked = require_snapshot_binding("orion", payload, binding)
        report = inspect_restore("orion", [], checked["records"])
        self.assertEqual(report["reopening"], "not_assessed")
        self.assertEqual(report["coverage"], "caller_supplied_only")
        checked["coverage"]["exclusions"].append("fixture:local-mutation")
        self.assertEqual(require_snapshot_binding("orion", payload, binding), value)
        self.assertEqual(binding, before)
        # Replacing both inputs can pass: no independently trusted registry exists.
        changed = encoded({**value, "removal_history_boundary": "fixture:older"})
        require_snapshot_binding("orion", changed, bind_snapshot_manifest("orion", changed))

    def test_saved_binding_survives_restart_and_originals_remain_identical(self):
        for mode in ("DELETE", "WAL"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                db_path = root / "invented.sqlite3"
                bank = MemoryBank(db_path)
                for profile in ("orion", "aurora"):
                    bank.add(profile, "source_note", "Invented original", "fixture:original")
                with sqlite3.connect(db_path) as db:
                    db.execute("PRAGMA journal_mode=" + mode)
                db.close()
                before = db_path.read_bytes()
                exports = {p: bank.export(p) for p in ("orion", "aurora")}
                payload = encoded(fixture())
                binding = bind_snapshot_manifest("orion", payload)
                input_path = root / "manifest.json"
                binding_path = root / "binding.json"
                input_path.write_bytes(payload)
                binding_path.write_text(json.dumps(binding), encoding="utf-8")
                binding_bytes = binding_path.read_bytes()
                script = ("import json,sys; from pathlib import Path; "
                          "from lumina_orion.snapshot_binding import require_snapshot_binding; "
                          "print(json.dumps(require_snapshot_binding('orion',"
                          "Path(sys.argv[1]).read_bytes(),json.loads(Path(sys.argv[2]).read_text()))))")
                for _ in range(2):
                    result = subprocess.run([sys.executable, "-c", script, str(input_path), str(binding_path)],
                                            capture_output=True, text=True, timeout=10, check=True)
                    self.assertEqual(json.loads(result.stdout), fixture())
                input_path.write_bytes(encoded({**fixture(), "snapshot_id": "fixture:changed"}))
                result = subprocess.run([sys.executable, "-c", script, str(input_path), str(binding_path)],
                                        capture_output=True, text=True, timeout=10)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Snapshot binding conflict", result.stderr)
                self.assertEqual(binding_path.read_bytes(), binding_bytes)
                self.assertEqual(db_path.read_bytes(), before)
                self.assertEqual({p: MemoryBank(db_path).export(p) for p in exports}, exports)


if __name__ == "__main__":
    unittest.main()

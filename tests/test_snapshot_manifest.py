import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lumina_orion.memory import MemoryBank
from lumina_orion.restore import inspect_restore
from lumina_orion.snapshot import validate_snapshot_manifest


def fixture(profile="orion"):
    return {"version": 1, "profile": profile, "snapshot_id": "fixture:snapshot-A",
            "removal_history_boundary": None,
            "coverage": {"scope": "profile_records", "declaration": "partial",
                         "exclusions": ["fixture:unmapped-results"]},
            "records": [{"profile": profile, "record_key": "fixture:record",
                         "target_key": "fixture:target", "content_sha256": "a" * 64}]}


class SnapshotManifestTests(unittest.TestCase):
    def test_normalization_detaches_nested_output_and_retains_explicit_limits(self):
        original = fixture(" ORION ")
        original["snapshot_id"] = " fixture:snapshot-A "
        original["removal_history_boundary"] = " fixture:boundary-old "
        before = copy.deepcopy(original)
        result = validate_snapshot_manifest("orion", original)
        self.assertEqual(result["profile"], "orion")
        self.assertEqual(result["snapshot_id"], "fixture:snapshot-A")
        self.assertEqual(result["removal_history_boundary"], "fixture:boundary-old")
        result["records"][0]["target_key"] = "changed"
        result["coverage"]["exclusions"].clear()
        self.assertEqual(original, before)

    def test_cross_profile_envelope_or_record_is_rejected_without_modification(self):
        for envelope in (True, False):
            value = fixture()
            if envelope:
                value["profile"] = "aurora"
            else:
                value["records"][0]["profile"] = "aurora"
            before = copy.deepcopy(value)
            with self.assertRaises(ValueError):
                validate_snapshot_manifest("orion", value)
            self.assertEqual(value, before)
        other = validate_snapshot_manifest("aurora", fixture("aurora"))
        self.assertEqual(other["records"][0]["profile"], "aurora")

    def test_missing_unknown_and_invalid_fields_fail_without_changing_input(self):
        invalid = []
        for name in fixture():
            value = fixture()
            del value[name]
            invalid.append(value)
        for field, bad in [("version", True), ("version", 2), ("snapshot_id", " "),
                           ("removal_history_boundary", "bad\nvalue")]:
            value = fixture()
            value[field] = bad
            invalid.append(value)
        value = fixture()
        value["payload"] = "invented unwanted field"
        invalid.append(value)
        value = fixture()
        value["records"][0]["content_sha256"] = "A" * 64
        invalid.append(value)
        value = fixture()
        value["records"].append(copy.deepcopy(value["records"][0]))
        invalid.append(value)
        value = fixture()
        value["coverage"]["scope"] = "all_backups"
        invalid.append(value)
        value = fixture()
        value["coverage"]["exclusions"].append(" fixture:unmapped-results ")
        invalid.append(value)
        for value in invalid:
            before = copy.deepcopy(value)
            with self.assertRaises(ValueError):
                validate_snapshot_manifest("orion", value)
            self.assertEqual(value, before)

    def test_complete_declaration_or_boundary_label_never_grants_clearance(self):
        value = fixture()
        value["coverage"]["declaration"] = "declared_complete"
        with self.assertRaises(ValueError):
            validate_snapshot_manifest("orion", value)
        value["coverage"]["exclusions"] = []
        value["records"] = []
        for boundary in (None, "fixture:old", "fixture:claims-current"):
            value["removal_history_boundary"] = boundary
            result = validate_snapshot_manifest("orion", value)
            self.assertEqual(result["removal_history_boundary"], boundary)
            report = inspect_restore("orion", [], result["records"])
            self.assertEqual(report["coverage"], "caller_supplied_only")
            self.assertEqual(report["reopening"], "not_assessed")

    def test_bounded_lists_and_record_fields(self):
        values = []
        for field, bad in [("records", ()), ("records", [None] * 10001)]:
            value = fixture()
            value[field] = bad
            values.append(value)
        value = fixture()
        value["coverage"]["exclusions"] = ["fixture:x"] * 1001
        values.append(value)
        value = fixture()
        value["records"][0]["source_text"] = "invented payload"
        values.append(value)
        for value in values:
            with self.assertRaises(ValueError):
                validate_snapshot_manifest("orion", value)

    def test_json_restart_reproduces_result_and_preserves_original_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "invented.sqlite3"
            bank = MemoryBank(path)
            for profile in ("orion", "aurora"):
                bank.add(profile, "source_note", "Invented original", "fixture:original")
            exports = {p: bank.export(p) for p in ("orion", "aurora")}
            database = path.read_bytes()
            input_path = root / "invented-manifest.json"
            input_path.write_text(json.dumps(fixture()), encoding="utf-8")
            input_bytes = input_path.read_bytes()
            expected = validate_snapshot_manifest("orion", fixture())
            script = ("import json,sys; from pathlib import Path; "
                      "from lumina_orion.snapshot import validate_snapshot_manifest; "
                      "m=json.loads(Path(sys.argv[1]).read_text()); "
                      "print(json.dumps(validate_snapshot_manifest('orion',m)))")
            for _ in range(2):
                result = subprocess.run([sys.executable, "-c", script, str(input_path)],
                                        check=True, capture_output=True, text=True, timeout=10)
                self.assertEqual(json.loads(result.stdout), expected)
            self.assertEqual(input_path.read_bytes(), input_bytes)
            self.assertEqual(path.read_bytes(), database)
            self.assertEqual({p: MemoryBank(path).export(p) for p in exports}, exports)


if __name__ == "__main__":
    unittest.main()

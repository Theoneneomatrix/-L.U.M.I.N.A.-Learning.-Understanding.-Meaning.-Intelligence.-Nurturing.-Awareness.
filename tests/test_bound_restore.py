import copy
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lumina_orion.bound_restore import inspect_bound_restore
from lumina_orion.history_binding import bind_removal_history
from lumina_orion.memory import MemoryBank
from lumina_orion.snapshot_binding import bind_snapshot_manifest


def fixture(profile="orion"):
    snapshot = {"version": 1, "profile": profile, "snapshot_id": "fixture:copy-A",
                "removal_history_boundary": "fixture:boundary-A",
                "coverage": {"scope": "profile_records", "declaration": "partial",
                             "exclusions": ["fixture:unmapped-results"]},
                "records": [{"profile": profile, "record_key": "fixture:record",
                             "target_key": "fixture:target", "content_sha256": "a" * 64}]}
    history = {"version": 1, "profile": profile, "history_boundary": "fixture:boundary-A",
               "requests": [{"version": 1, "profile": profile, "request_id": "fixture:request",
                             "target_key": "fixture:target", "content_sha256": "a" * 64,
                             "state": "requested"}]}
    return snapshot, history


def encoded(value):
    return json.dumps(value).encode("utf-8")


def arguments(snapshot, history, profile="orion"):
    s, h = encoded(snapshot), encoded(history)
    return dict(profile=profile, snapshot_payload=s,
                expected_snapshot=bind_snapshot_manifest(profile, s),
                history_payload=h, expected_history=bind_removal_history(profile, h))


class BoundRestoreTests(unittest.TestCase):
    def test_exact_report_identifies_copy_and_both_bindings_without_clearance(self):
        args = arguments(*fixture())
        self.assertEqual(inspect_bound_restore(**args), {
            "version": 1, "profile": "orion", "snapshot_id": "fixture:copy-A",
            "snapshot_binding": args["expected_snapshot"],
            "history_binding": args["expected_history"],
            "snapshot_history_boundary": "fixture:boundary-A",
            "declared_coverage": {"scope": "profile_records", "declaration": "partial",
                                  "exclusions": ["fixture:unmapped-results"]},
            "read_only": True, "comparison_status": "bound_declarations_compared",
            "coverage": "caller_supplied_only", "authority": "caller_asserted_only",
            "freshness": "not_assessed", "reopening": "not_assessed",
            "findings": [{"request_id": "fixture:request", "status": "requested_version_present",
                          "matching_record_keys": ["fixture:record"]}]})

    def test_all_existing_comparison_outcomes_are_declaration_only(self):
        for outcome in ("requested_version_present", "ambiguous_match",
                        "different_version_listed", "target_not_listed"):
            s, h = fixture()
            if outcome == "ambiguous_match":
                s["records"].append(dict(s["records"][0], record_key="fixture:second"))
            elif outcome == "different_version_listed":
                s["records"][0]["content_sha256"] = "b" * 64
            elif outcome == "target_not_listed":
                s["records"] = []
            with self.subTest(outcome=outcome):
                report = inspect_bound_restore(**arguments(s, h))
                self.assertEqual(report["findings"][0]["status"], outcome)
                self.assertEqual(report["authority"], "caller_asserted_only")
                self.assertEqual(report["reopening"], "not_assessed")

    def test_missing_history_is_unresolved_not_empty_findings(self):
        args = arguments(*fixture())
        args.pop("history_payload"); args.pop("expected_history")
        report = inspect_bound_restore(**args)
        self.assertEqual(report["comparison_status"], "history_missing")
        self.assertIsNone(report["history_binding"])
        self.assertIsNone(report["findings"])
        for field in ("history_payload", "expected_history"):
            partial = arguments(*fixture()); partial.pop(field)
            with self.assertRaisesRegex(ValueError, "together"):
                inspect_bound_restore(**partial)

    def test_unspecified_boundary_stays_unresolved_but_checks_supplied_bindings(self):
        s, h = fixture(); s["removal_history_boundary"] = None
        args = arguments(s, h)
        report = inspect_bound_restore(**args)
        self.assertEqual(report["comparison_status"], "snapshot_boundary_unspecified")
        self.assertIsNone(report["findings"])
        self.assertEqual(report["history_binding"], args["expected_history"])
        h["requests"] = []
        args["history_payload"] = encoded(h)
        with self.assertRaisesRegex(ValueError, "conflict"):
            inspect_bound_restore(**args)

    def test_boundary_conflict_rejected_even_when_both_bindings_match(self):
        s, h = fixture(); h["history_boundary"] = "fixture:boundary-B"
        with self.assertRaisesRegex(ValueError, "boundary conflict"):
            inspect_bound_restore(**arguments(s, h))

    def test_changed_payloads_malformed_bindings_and_profiles_fail(self):
        for field in ("snapshot_payload", "history_payload"):
            args = arguments(*fixture())
            value = json.loads(args[field]); value["records" if field == "snapshot_payload" else "requests"] = []
            args[field] = encoded(value)
            with self.assertRaisesRegex(ValueError, "conflict"):
                inspect_bound_restore(**args)
            args[field] = b'{"version":1,"version":1}'
            with self.assertRaises(ValueError):
                inspect_bound_restore(**args)
        for field in ("expected_snapshot", "expected_history"):
            args = arguments(*fixture()); args[field] = None
            with self.assertRaises(ValueError):
                inspect_bound_restore(**args)
        foreign = arguments(*fixture("aurora"), profile="aurora")
        for fields in (("profile",), ("history_payload", "expected_history"),
                       ("snapshot_payload", "expected_snapshot")):
            args = arguments(*fixture())
            for field in fields:
                args[field] = foreign[field]
            with self.assertRaises(ValueError):
                inspect_bound_restore(**args)

    def test_empty_complete_and_replaced_inputs_never_establish_current_authority(self):
        s, h = fixture()
        original = arguments(s, h)
        # Negative control: replacing omitted declarations AND their expected
        # bindings still matches. This is not independent rollback protection.
        s["records"] = []; h["requests"] = []
        s["coverage"] = {"scope": "profile_records", "declaration": "declared_complete", "exclusions": []}
        replacement = arguments(s, h)
        self.assertNotEqual(original["expected_history"], replacement["expected_history"])
        report = inspect_bound_restore(**replacement)
        self.assertEqual(report["comparison_status"], "bound_declarations_compared")
        self.assertEqual(report["findings"], [])
        self.assertEqual(report["declared_coverage"]["declaration"], "declared_complete")
        self.assertEqual(report["coverage"], "caller_supplied_only")
        self.assertEqual(report["authority"], "caller_asserted_only")
        self.assertEqual(report["freshness"], "not_assessed")
        self.assertEqual(report["reopening"], "not_assessed")

    def test_report_detached_inputs_unchanged_and_profiles_normalized(self):
        args = arguments(*fixture()); args["profile"] = " ORION "
        before = copy.deepcopy(args)
        report = inspect_bound_restore(**args)
        report["snapshot_binding"].clear(); report["history_binding"].clear()
        report["declared_coverage"]["exclusions"].clear()
        report["findings"][0]["matching_record_keys"].clear()
        self.assertEqual(args, before)
        self.assertEqual(inspect_bound_restore(**args)["findings"][0]["matching_record_keys"], ["fixture:record"])

    def test_saved_inputs_restart_reproduce_report_and_preserve_originals_delete_wal(self):
        script = '''import json,sys
from pathlib import Path
from lumina_orion.bound_restore import inspect_bound_restore
x=json.loads(Path(sys.argv[1]).read_text())
x['snapshot_payload']=x['snapshot_payload'].encode()
x['history_payload']=x['history_payload'].encode()
print(json.dumps(inspect_bound_restore(**x)))
'''
        profiles = ("orion", "aurora", "theo", "elysia", "selene")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for mode in ("DELETE", "WAL"):
                db_path = root / (mode + ".sqlite3"); bank = MemoryBank(db_path)
                for profile in profiles:
                    bank.add(profile, "source_note", "Invented original", "fixture:original")
                with sqlite3.connect(db_path) as db:
                    db.execute("PRAGMA journal_mode=" + mode)
                exports = {p: bank.export(p) for p in profiles}; original = db_path.read_bytes()
                for profile in profiles:
                    args = arguments(*fixture(profile), profile=profile)
                    expected = inspect_bound_restore(**args)
                    saved = dict(args, snapshot_payload=args["snapshot_payload"].decode(),
                                 history_payload=args["history_payload"].decode())
                    path = root / (mode + "-" + profile + ".json")
                    path.write_text(json.dumps(saved), encoding="utf-8"); payload = path.read_bytes()
                    for _ in range(2):
                        r = subprocess.run([sys.executable, "-c", script, str(path)],
                                           check=True, capture_output=True, text=True, timeout=10)
                        self.assertEqual(json.loads(r.stdout), expected)
                    self.assertEqual(path.read_bytes(), payload)
                    if profile != "orion":
                        with self.assertRaises(ValueError):
                            inspect_bound_restore(**dict(args, profile="orion"))
                self.assertEqual(db_path.read_bytes(), original)
                self.assertEqual({p: MemoryBank(db_path).export(p) for p in profiles}, exports)


if __name__ == "__main__":
    unittest.main()

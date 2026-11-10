import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lumina_orion.memory import MemoryBank
from lumina_orion.removal import RemovalIntentLedger
from lumina_orion.restore import inspect_restore


def request(**changes):
    row = dict(version=1, state="requested", profile="orion",
               request_id="fixture-request", target_key="fixture:source",
               content_sha256="a" * 64)
    row.update(changes)
    return row


def record(**changes):
    row = dict(profile="orion", record_key="fixture:record",
               target_key="fixture:source", content_sha256="a" * 64)
    row.update(changes)
    return row


class RestoreInspectionTests(unittest.TestCase):
    def test_exact_match_reports_present_not_removal_or_permission(self):
        result = inspect_restore(" ORION ", [request()], [record()])
        self.assertEqual(result["profile"], "orion")
        self.assertTrue(result["read_only"])
        self.assertEqual(result["coverage"], "caller_supplied_only")
        self.assertEqual(result["reopening"], "not_assessed")
        self.assertEqual(result["findings"], [{
            "request_id": "fixture-request", "status": "requested_version_present",
            "matching_record_keys": ["fixture:record"]}])

    def test_multiple_exact_candidates_are_ambiguous_and_deterministic(self):
        records = [record(record_key="fixture:z"), record(record_key="fixture:a")]
        before = copy.deepcopy(records)
        result = inspect_restore("orion", [request()], records)
        self.assertEqual(result["findings"][0]["status"], "ambiguous_match")
        self.assertEqual(result["findings"][0]["matching_record_keys"],
                         ["fixture:a", "fixture:z"])
        self.assertEqual(inspect_restore("orion", [request()], list(reversed(records))),
                         result)
        self.assertEqual(records, before)

    def test_missing_or_other_version_does_not_clear_a_request(self):
        for records, status in [
            ([], "target_not_listed"),
            ([record(content_sha256="b" * 64)], "different_version_listed"),
            ([record(target_key="fixture:unrelated")], "target_not_listed"),
        ]:
            with self.subTest(status=status):
                result = inspect_restore("orion", [request()], records)
                self.assertEqual(result["findings"][0]["status"], status)
                self.assertEqual(result["findings"][0]["matching_record_keys"], [])
                self.assertEqual(result["reopening"], "not_assessed")
        empty = inspect_restore("orion", [], [])
        self.assertEqual(empty["findings"], [])
        self.assertEqual(empty["reopening"], "not_assessed")

    def test_profile_collisions_neither_match_nor_leak_foreign_records(self):
        requests = [request(), request(profile="aurora", request_id="foreign")]
        records = [record(profile="aurora", record_key="foreign"),
                   record(content_sha256="b" * 64)]
        before = copy.deepcopy((requests, records))
        result = inspect_restore("orion", requests, records)
        self.assertEqual(len(result["findings"]), 1)
        self.assertEqual(result["findings"][0]["status"], "different_version_listed")
        self.assertNotIn("foreign", json.dumps(result))
        other = inspect_restore("aurora", requests, records)
        self.assertEqual(other["findings"][0]["status"], "requested_version_present")
        self.assertEqual((requests, records), before)

    def test_invalid_or_conflicting_mappings_preserve_inputs(self):
        cases = [
            ("unknown", [request()], [record()]),
            ("orion", None, []),
            ("orion", [request(version=True)], []),
            ("orion", [request(version=2)], []),
            ("orion", [request(state="removed")], []),
            ("orion", [request(content_sha256="A" * 64)], []),
            ("orion", [request(request_id="bad\nidentity")], []),
            ("orion", [request(), request(target_key="changed")], []),
            ("orion", [request()], [record(), record(content_sha256="b" * 64)]),
            ("orion", [request()], [record(content_sha256="bad")]),
            ("orion", [request()], [None]),
        ]
        for profile, requests, records in cases:
            with self.subTest(profile=profile, requests=requests):
                before = copy.deepcopy((requests, records))
                with self.assertRaises(ValueError):
                    inspect_restore(profile, requests, records)
                self.assertEqual((requests, records), before)

    def test_old_snapshot_can_reintroduce_requested_version(self):
        intents = [request()]
        current = [record(content_sha256="b" * 64)]
        old = [record()]
        self.assertEqual(inspect_restore("orion", intents, current)["findings"][0]["status"],
                         "different_version_listed")
        self.assertEqual(inspect_restore("orion", intents, old)["findings"][0]["status"],
                         "requested_version_present")
        # An old ledger with no requests cannot establish fresh permission.
        missing_history = inspect_restore("orion", [], old)
        self.assertEqual(missing_history["reopening"], "not_assessed")
        self.assertEqual(missing_history["coverage"], "caller_supplied_only")

    def test_database_originals_and_fresh_process_reproduction(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invented.sqlite3"
            bank = MemoryBank(path)
            for profile in ("orion", "aurora"):
                bank.add(profile, "source_note", "Invented original", "fixture:original")
            before_export = {p: bank.export(p) for p in ("orion", "aurora")}
            ledger = RemovalIntentLedger(path)
            ledger.record("orion", "fixture-request", target_key="fixture:source",
                          content_sha256="a" * 64)
            ledger.record("aurora", "fixture-request", target_key="fixture:source",
                          content_sha256="b" * 64)
            intents = ledger.intents("orion") + ledger.intents("aurora")
            records = [record(), record(profile="aurora", record_key="foreign")]
            input_path = Path(directory) / "invented-input.json"
            input_path.write_text(json.dumps([intents, records]), encoding="utf-8")
            input_bytes = input_path.read_bytes()
            database_bytes = path.read_bytes()
            expected = inspect_restore("orion", intents, records)
            script = (
                "import json,sys; from lumina_orion.restore import inspect_restore; "
                "from pathlib import Path; "
                "requests,records=json.loads(Path(sys.argv[1]).read_text()); "
                "print(json.dumps(inspect_restore('orion',requests,records)))"
            )
            for _ in range(2):
                run = subprocess.run([sys.executable, "-c", script, str(input_path)],
                                     check=True, capture_output=True, text=True, timeout=10)
                self.assertEqual(json.loads(run.stdout), expected)
            self.assertEqual(input_path.read_bytes(), input_bytes)
            self.assertEqual(path.read_bytes(), database_bytes)
            self.assertEqual({p: MemoryBank(path).export(p) for p in before_export},
                             before_export)
            self.assertEqual(ledger.intents("orion") + ledger.intents("aurora"), intents)


if __name__ == "__main__":
    unittest.main()

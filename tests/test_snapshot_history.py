import copy
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lumina_orion.memory import MemoryBank
from lumina_orion.snapshot_binding import bind_snapshot_manifest
from lumina_orion.snapshot_history import inspect_snapshot_history
from lumina_orion.snapshot_registry import SnapshotBindingRegistry


def payload(profile="orion", boundary="fixture:old"):
    return json.dumps({"version": 1, "profile": profile, "snapshot_id": "fixture:A",
                       "removal_history_boundary": boundary,
                       "coverage": {"scope": "profile_records", "declaration": "declared_complete",
                                    "exclusions": []}, "records": []}).encode()


def observation(profile="orion", state="current", boundary="fixture:old"):
    return dict(version=1, profile=profile, state=state, history_boundary=boundary)


def check(p, obs):
    return inspect_snapshot_history("orion", p, bind_snapshot_manifest("orion", p), obs)


class SnapshotHistoryTests(unittest.TestCase):
    def test_missing_stale_unavailable_conflict_and_unspecified_are_not_clearance(self):
        for obs, expected in [(None, "observation_missing"),
                              (observation(state="stale"), "observation_stale"),
                              (observation(state="unavailable", boundary=None), "observation_unavailable"),
                              (observation(boundary="fixture:new"), "boundary_conflict")]:
            with self.subTest(expected=expected):
                result = check(payload(), obs)
                self.assertEqual(result["history_status"], expected)
                self.assertEqual(result["reopening"], "not_assessed")
        self.assertEqual(check(payload(boundary=None), observation())["history_status"],
                         "declaration_boundary_unspecified")

    def test_matching_and_replayed_assertions_never_authenticate_or_authorize(self):
        # Negative control: both old inputs agree, so label comparison cannot detect replay.
        result = check(payload(), observation())
        self.assertEqual(result["history_status"], "boundary_matches_assertion")
        self.assertEqual(result["authority"], "caller_asserted_only")
        self.assertEqual(result["reopening"], "not_assessed")
        self.assertTrue(result["read_only"])
        self.assertNotIn("authorized", result)
        # An independently supplied newer boundary exposes disagreement, not its cause/order.
        self.assertEqual(check(payload(), observation(boundary="fixture:new"))["history_status"],
                         "boundary_conflict")

    def test_strict_shapes_profiles_and_labels(self):
        bad = [[], {}, observation("aurora"), observation(state="verified"),
               observation(boundary=None), observation(boundary="x\ny"),
               observation(boundary="x" * 201)]
        for key in observation():
            value = observation(); del value[key]; bad.append(value)
        for value in (True, 2, 1.0):
            value_obs = observation(); value_obs["version"] = value; bad.append(value_obs)
        extra = observation(); extra["source_text"] = "invented"; bad.append(extra)
        for obs in bad:
            with self.subTest(obs=obs), self.assertRaises(ValueError):
                check(payload(), obs)
        p = payload(" ORION ", " fixture:old ")
        self.assertEqual(check(p, observation(" ORION ", boundary=" fixture:old "))["history_status"],
                         "boundary_matches_assertion")

    def test_binding_conflict_cannot_be_hidden_by_missing_or_matching_observation(self):
        original = payload()
        expected = bind_snapshot_manifest("orion", original)
        for obs in (None, observation(boundary="fixture:new")):
            with self.assertRaises(ValueError):
                inspect_snapshot_history("orion", payload(boundary="fixture:new"), expected, obs)

    def test_detached_outputs_and_unchanged_inputs(self):
        p = payload(); binding = bind_snapshot_manifest("orion", p); obs = observation()
        before = copy.deepcopy((p, binding, obs))
        result = inspect_snapshot_history("orion", p, binding, obs)
        result["history_status"] = "local mutation"
        self.assertEqual((p, binding, obs), before)
        self.assertEqual(check(p, obs)["history_status"], "boundary_matches_assertion")

    def test_restart_rolled_back_snapshot_and_originals_in_both_modes(self):
        script = '''import json,sys
from lumina_orion.snapshot_history import inspect_snapshot_history
print(json.dumps(inspect_snapshot_history(sys.argv[1],sys.argv[2].encode(),json.loads(sys.argv[3]),json.loads(sys.argv[4]))))
'''
        with tempfile.TemporaryDirectory() as directory:
            for mode in ("DELETE", "WAL"):
                path = Path(directory) / (mode + ".sqlite3")
                bank = MemoryBank(path); registry = SnapshotBindingRegistry(path)
                for profile in ("orion", "aurora", "theo", "elysia", "selene"):
                    bank.add(profile, "source_note", "Invented original", "fixture:original")
                    registry.record(profile, payload(profile))
                with sqlite3.connect(path) as db:
                    db.execute("PRAGMA journal_mode=" + mode)
                originals = {p: bank.export(p) for p in ("orion", "aurora", "theo", "elysia", "selene")}
                before = path.read_bytes()
                for profile in originals:
                    binding = registry.get(profile, "fixture:A")
                    for _ in range(2):
                        result = subprocess.run([sys.executable, "-c", script, profile,
                            payload(profile).decode(), json.dumps(binding),
                            json.dumps(observation(profile, boundary="fixture:new"))],
                            capture_output=True, text=True, check=True, timeout=10)
                        report = json.loads(result.stdout)
                        self.assertEqual(report["history_status"], "boundary_conflict")
                        self.assertEqual(report["profile"], profile)
                        self.assertEqual(report["reopening"], "not_assessed")
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual({p: MemoryBank(path).export(p) for p in originals}, originals)
                self.assertEqual(SnapshotBindingRegistry(path).get("orion", "fixture:A"),
                                 registry.get("orion", "fixture:A"))


if __name__ == "__main__":
    unittest.main()

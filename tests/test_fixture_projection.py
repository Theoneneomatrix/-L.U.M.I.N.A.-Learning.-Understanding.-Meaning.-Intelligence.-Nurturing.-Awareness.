import copy
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lumina_orion.bound_restore import inspect_bound_restore
from lumina_orion.fixture_projection import project_fixture_declarations
from lumina_orion.history_binding import bind_removal_history
from lumina_orion.memory import MemoryBank
from lumina_orion.removal import RemovalIntentLedger
from lumina_orion.snapshot_binding import bind_snapshot_manifest


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def fixture(profile="orion"):
    return dict(profile=profile,
                memory_export={"format": "lumina-memory-v1", "profile": profile,
                               "records": [{"id": 1, "profile": profile, "text": "Invented é😀"}],
                               "results": []},
                saved_intents=[{"id": 1, "version": 1, "profile": profile,
                                "request_id": "fixture:r1", "target_key": "fixture:target",
                                "content_sha256": digest("Invented é😀"),
                                "created_at": "fixture:time", "state": "requested"}],
                mappings=[{"note_id": 1, "record_key": "fixture:note1", "target_key": "fixture:target",
                           "content_sha256": digest("Invented é😀")}],
                snapshot_id="fixture:copy", history_boundary="fixture:boundary")


def inspect(value, profile="orion"):
    s = json.dumps(value["snapshot"]).encode(); h = json.dumps(value["history"]).encode()
    return inspect_bound_restore(profile, s, bind_snapshot_manifest(profile, s),
                                 history_payload=h, expected_history=bind_removal_history(profile, h))


class FixtureProjectionTests(unittest.TestCase):
    def test_exact_projection_and_existing_inspector_integration(self):
        args = fixture(); args["profile"] = " ORION "
        value = project_fixture_declarations(**args)
        self.assertEqual(value["snapshot"]["records"], [dict(profile="orion", record_key="fixture:note1",
                         target_key="fixture:target", content_sha256=digest("Invented é😀"))])
        self.assertEqual(value["history"]["requests"], [{k: v for k, v in args["saved_intents"][0].items()
                                                        if k not in ("id", "created_at")}])
        self.assertEqual(value["snapshot"]["coverage"], {"scope": "profile_records", "declaration": "partial",
                         "exclusions": ["fixture:research-results-and-untracked-copies"]})
        report = inspect(value)
        self.assertEqual(report["findings"][0]["status"], "requested_version_present")
        self.assertEqual(report["authority"], "caller_asserted_only")
        self.assertEqual(report["freshness"], "not_assessed")
        self.assertEqual(report["reopening"], "not_assessed")
        self.assertNotIn("Invented", json.dumps(value))

    def test_missing_extra_and_ambiguous_mappings_fail(self):
        bad = []
        x = fixture(); x["mappings"] = []; bad.append(x)
        x = fixture(); x["mappings"][0]["note_id"] = 2; bad.append(x)
        x = fixture(); x["mappings"] *= 2; bad.append(x)
        x = fixture(); x["memory_export"]["records"] *= 2; bad.append(x)
        for field in ("record_key", "target_key"):
            x = fixture()
            x["memory_export"]["records"].append(dict(x["memory_export"]["records"][0], id=2))
            m = dict(x["mappings"][0], note_id=2, record_key="fixture:second", target_key="fixture:second")
            m[field] = x["mappings"][0][field]; x["mappings"].append(m); bad.append(x)
        for args in bad:
            before = copy.deepcopy(args)
            with self.assertRaises(ValueError):
                project_fixture_declarations(**args)
            self.assertEqual(args, before)

    def test_changed_text_same_id_and_exact_unicode_bytes(self):
        for text in ("Invented e\u0301😀", " Invented é😀", "Invented é😀 ", "Changed"):
            args = fixture(); args["memory_export"]["records"][0]["text"] = text
            with self.assertRaisesRegex(ValueError, "digest conflict"):
                project_fixture_declarations(**args)
        args["mappings"][0]["content_sha256"] = digest(text)
        result = project_fixture_declarations(**args)
        self.assertEqual(result["snapshot"]["records"][0]["content_sha256"], digest(text))

    def test_profile_conflicts_in_all_source_locations(self):
        for location in ("export", "note", "intent"):
            args = fixture()
            if location == "export":
                args["memory_export"]["profile"] = "aurora"
            elif location == "note":
                args["memory_export"]["records"][0]["profile"] = "aurora"
            else:
                args["saved_intents"][0]["profile"] = "aurora"
            with self.assertRaises(ValueError):
                project_fixture_declarations(**args)

    def test_invalid_shapes_ids_unicode_and_bounds(self):
        bad = []
        for key, value in (("saved_intents", None), ("mappings", ()), ("history_boundary", None),
                           ("snapshot_id", "x" * 201)):
            x = fixture(); x[key] = value; bad.append(x)
        for key, value in (("id", True), ("id", 0), ("text", ""), ("text", "x" * 20001),
                           ("text", "\ud800")):
            x = fixture(); x["memory_export"]["records"][0][key] = value; bad.append(x)
        for key, value in (("note_id", True), ("extra", 1), ("content_sha256", "A" * 64)):
            x = fixture(); x["mappings"][0][key] = value; bad.append(x)
        x = fixture(); x["saved_intents"][0]["version"] = True; bad.append(x)
        x = fixture(); x["saved_intents"][0].pop("created_at"); bad.append(x)
        x = fixture(); x["saved_intents"] *= 2; bad.append(x)
        x = fixture(); x["memory_export"]["records"] = [None] * 10001; bad.append(x)
        x = fixture(); x["mappings"][0]["record_key"] = "x" * 2000
        x["memory_export"]["records"] = [dict(id=i, profile="orion", text="Invented é😀") for i in range(1, 700)]
        x["mappings"] = [dict(x["mappings"][0], note_id=i, record_key=str(i)+"x"*1900,
                              target_key=str(i)+"x"*1900) for i in range(1, 700)]; bad.append(x)
        for args in bad:
            with self.subTest(keys=list(args)), self.assertRaises(ValueError):
                project_fixture_declarations(**args)

    def test_versions_shared_target_sorting_and_detached_results(self):
        args = fixture(); args["memory_export"]["records"].append(dict(id=2, profile="orion", text="Revised"))
        args["mappings"].insert(0, dict(note_id=2, record_key="fixture:note2", target_key="fixture:target",
                                      content_sha256=digest("Revised")))
        before = copy.deepcopy(args); result = project_fixture_declarations(**args)
        self.assertEqual([r["record_key"] for r in result["snapshot"]["records"]], ["fixture:note1", "fixture:note2"])
        result["history"]["requests"].clear(); result["snapshot"]["coverage"]["exclusions"].clear()
        self.assertEqual(args, before)
        self.assertEqual(inspect(project_fixture_declarations(**args))["findings"][0]["status"], "requested_version_present")

    def test_empty_or_replaced_inputs_and_metadata_are_not_trusted_coverage(self):
        args = fixture(); original = project_fixture_declarations(**args)
        args["memory_export"]["records"][0]["source"] = "fixture:changed-metadata"
        self.assertEqual(project_fixture_declarations(**args), original)  # Text digest is NOT metadata identity.
        args["memory_export"]["records"] = []; args["mappings"] = []; args["saved_intents"] = []
        report = inspect(project_fixture_declarations(**args))
        self.assertEqual(report["findings"], [])
        self.assertEqual(report["declared_coverage"]["declaration"], "partial")
        self.assertEqual(report["coverage"], "caller_supplied_only")
        self.assertEqual(report["reopening"], "not_assessed")

    def test_saved_exports_restart_and_originals_unchanged_for_all_profiles_delete_wal(self):
        script = '''import json,sys
from pathlib import Path
from lumina_orion.fixture_projection import project_fixture_declarations
print(json.dumps(project_fixture_declarations(**json.loads(Path(sys.argv[1]).read_text()))))
'''
        profiles = ("orion", "aurora", "theo", "elysia", "selene")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for mode in ("DELETE", "WAL"):
                path = root / (mode + ".sqlite3"); bank = MemoryBank(path); ledger = RemovalIntentLedger(path)
                mappings = {}
                for profile in profiles:
                    note_id = bank.add(profile, "source_note", "Invented é😀", "fixture:original")
                    ledger.record(profile, "fixture:r1", target_key="fixture:target", content_sha256=digest("Invented é😀"))
                    mappings[profile] = [dict(note_id=note_id, record_key="fixture:note", target_key="fixture:target",
                                              content_sha256=digest("Invented é😀"))]
                with sqlite3.connect(path) as db:
                    db.execute("PRAGMA journal_mode=" + mode)
                exports = {p: bank.export(p) for p in profiles}; intents = {p: ledger.intents(p) for p in profiles}
                original = path.read_bytes()
                for profile in profiles:
                    args = dict(profile=profile, memory_export=exports[profile], saved_intents=intents[profile],
                                mappings=mappings[profile], snapshot_id="fixture:copy", history_boundary="fixture:boundary")
                    expected = project_fixture_declarations(**args)
                    saved = root / (mode + profile + ".json"); saved.write_text(json.dumps(args)); saved_bytes = saved.read_bytes()
                    for _ in range(2):
                        r = subprocess.run([sys.executable, "-c", script, str(saved)], check=True,
                                           capture_output=True, text=True, timeout=10)
                        self.assertEqual(json.loads(r.stdout), expected)
                    self.assertEqual(saved.read_bytes(), saved_bytes)
                self.assertEqual(path.read_bytes(), original)
                self.assertEqual({p: MemoryBank(path).export(p) for p in profiles}, exports)
                self.assertEqual({p: RemovalIntentLedger(path).intents(p) for p in profiles}, intents)


if __name__ == "__main__":
    unittest.main()

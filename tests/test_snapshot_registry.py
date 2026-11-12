import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Barrier

from lumina_orion.memory import MemoryBank
from lumina_orion.snapshot_registry import SnapshotBindingRegistry


def payload(profile="orion", boundary=None):
    return json.dumps({"version": 1, "profile": profile, "snapshot_id": "fixture:A",
                       "removal_history_boundary": boundary,
                       "coverage": {"scope": "profile_records", "declaration": "partial",
                                    "exclusions": []}, "records": []}).encode()


class SnapshotRegistryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "invented.sqlite3"
        self.registry = SnapshotBindingRegistry(self.path)

    def rows(self):
        with sqlite3.connect(self.path) as db:
            return db.execute("SELECT * FROM snapshot_bindings ORDER BY profile,snapshot_id").fetchall()

    def test_identical_retry_normalization_and_fresh_process_reads(self):
        first = self.registry.record("orion", payload())
        before = self.rows()
        self.assertEqual(self.registry.record(" ORION ", payload(" ORION ")), first)
        self.assertEqual(self.registry.get("orion", " fixture:A "), first)
        script = ("import json,sys; from lumina_orion.snapshot_registry import SnapshotBindingRegistry; "
                  "print(json.dumps(SnapshotBindingRegistry(sys.argv[1]).get('orion','fixture:A')))")
        for _ in range(2):
            r = subprocess.run([sys.executable, "-c", script, str(self.path)], check=True,
                               capture_output=True, text=True, timeout=10)
            self.assertEqual(json.loads(r.stdout), first)
        self.assertEqual(self.rows(), before)
        first["manifest_sha256"] = "b" * 64
        self.assertEqual(self.rows(), before)

    def test_changed_identity_binding_conflicts_and_missing_verify_never_registers(self):
        with self.assertRaisesRegex(ValueError, "No registered"):
            self.registry.verify("orion", payload())
        self.assertEqual(self.rows(), [])
        original = self.registry.record("orion", payload())
        for action in (self.registry.record, self.registry.verify):
            with self.assertRaises(ValueError):
                action("orion", payload(boundary="fixture:changed"))
        self.assertEqual(self.registry.get("orion", "fixture:A"), original)
        checked = self.registry.verify("orion", payload())
        checked["coverage"]["exclusions"].append("local mutation")
        self.assertEqual(self.registry.verify("orion", payload())["coverage"]["exclusions"], [])

    def test_invalid_input_and_profile_mismatch_preserve_registry(self):
        original = self.registry.record("orion", payload())
        before = self.rows()
        for bad in (b"{}", payload().replace(b'"version": 1', b'"version":1,"version":1'),
                    payload("aurora"), None):
            for action in (self.registry.record, self.registry.verify):
                with self.assertRaises(ValueError):
                    action("orion", bad)
        for profile, key in (("unknown", "fixture:A"), ("orion", ""), ("orion", "a\nb")):
            with self.assertRaises(ValueError):
                self.registry.get(profile, key)
        self.assertEqual(self.rows(), before)
        self.assertEqual(self.registry.get("orion", "fixture:A"), original)

    def test_additive_table_profile_scope_and_original_notes_preserved(self):
        path = Path(self.directory.name) / "legacy.sqlite3"
        bank = MemoryBank(path)
        profiles = ("orion", "aurora", "theo", "elysia", "selene")
        for p in profiles:
            bank.add(p, "source_note", "Invented original", "fixture:original")
        before = {p: bank.export(p) for p in profiles}
        registry = SnapshotBindingRegistry(path)
        for p in profiles:
            self.assertIsNone(registry.get(p, "fixture:A"))
            saved = registry.record(p, payload(p))
            self.assertEqual(saved["profile"], p)
            self.assertEqual(registry.verify(p, payload(p))["profile"], p)
        with self.assertRaises(ValueError):
            registry.verify("theo", payload("aurora"))
        self.assertEqual({p: MemoryBank(path).export(p) for p in profiles}, before)
        with sqlite3.connect(path) as db:
            columns = [r[1] for r in db.execute("PRAGMA table_info(snapshot_bindings)")]
            self.assertEqual(columns, ["version", "scheme", "profile", "snapshot_id", "manifest_sha256"])

    def test_competing_writers_same_and_changed_bindings_in_both_modes(self):
        for mode in ("DELETE", "WAL"):
            for changed in (False, True):
                path = Path(self.directory.name) / f"{mode}-{changed}.sqlite3"
                registry = SnapshotBindingRegistry(path)
                with sqlite3.connect(path) as db:
                    db.execute("PRAGMA journal_mode=" + mode)
                barrier = Barrier(2)
                def write(boundary):
                    barrier.wait(timeout=10)
                    try:
                        return registry.record("orion", payload(boundary=boundary))
                    except ValueError:
                        return "conflict"
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(write, (None, "fixture:new" if changed else None)))
                self.assertEqual(results.count("conflict"), int(changed))
                successful = [r for r in results if isinstance(r, dict)]
                self.assertTrue(all(r == successful[0] for r in successful))
                self.assertEqual(registry.get("orion", "fixture:A"), successful[0])
                with sqlite3.connect(path) as db:
                    self.assertEqual(db.execute("SELECT count(*) FROM snapshot_bindings").fetchone()[0], 1)

    def test_exception_before_commit_rolls_back_binding(self):
        connect = self.registry._connect
        @contextmanager
        def fail():
            with connect() as db:
                yield db
                raise RuntimeError("fixture failure")
        self.registry._connect = fail
        with self.assertRaises(RuntimeError):
            self.registry.record("orion", payload())
        self.assertIsNone(SnapshotBindingRegistry(self.path).get("orion", "fixture:A"))

    def test_process_exit_before_and_after_commit_preserves_profiles_and_retry(self):
        for mode in ("DELETE", "WAL"):
            for committed in (False, True):
                with self.subTest(mode=mode, committed=committed):
                    path = Path(self.directory.name) / f"crash-{mode}-{committed}.sqlite3"
                    registry = SnapshotBindingRegistry(path)
                    kept = registry.record("aurora", payload("aurora"))
                    with sqlite3.connect(path) as db:
                        db.execute("PRAGMA journal_mode=" + mode)
                    script = '''import os,sys
from contextlib import contextmanager
from lumina_orion.snapshot_registry import SnapshotBindingRegistry
r=SnapshotBindingRegistry(sys.argv[1])
if sys.argv[2]=='before':
 connect=r._connect
 @contextmanager
 def crash():
  with connect() as db:
   db.set_trace_callback(lambda sql: os._exit(71) if sql=='COMMIT' else None)
   yield db
 r._connect=crash
r.record('orion',sys.argv[3].encode())
os._exit(72)
'''
                    result = subprocess.run([sys.executable, "-c", script, str(path),
                                             "after" if committed else "before", payload().decode()],
                                            capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 72 if committed else 71, result.stderr)
                    reopened = SnapshotBindingRegistry(path)
                    self.assertEqual(reopened.get("orion", "fixture:A") is not None, committed)
                    retried = reopened.record("orion", payload())
                    self.assertEqual(reopened.get("orion", "fixture:A"), retried)
                    self.assertEqual(reopened.get("aurora", "fixture:A"), kept)


if __name__ == "__main__":
    unittest.main()

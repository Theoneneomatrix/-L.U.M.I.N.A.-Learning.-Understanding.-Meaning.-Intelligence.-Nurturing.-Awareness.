import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

from lumina_orion.memory import MemoryBank
from lumina_orion.removal import RemovalIntentLedger


class RemovalIntentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "invented.sqlite3"
        self.ledger = RemovalIntentLedger(self.path)

    def save(self, **changes):
        args = dict(profile="orion", request_id="request-A", target_key="fixture:source-A",
                    content_sha256="a" * 64)
        args.update(changes)
        return self.ledger.record(**args)

    def test_retry_preserves_identity_and_time_across_process_restart(self):
        first = self.save()
        self.assertEqual(first["version"], 1)
        self.assertEqual(first["state"], "requested")
        self.assertEqual(self.save(profile=" ORION ", request_id=" request-A ",
                                   target_key=" fixture:source-A "), first)
        script = ("import json,sys; from lumina_orion.removal import RemovalIntentLedger; "
                  "print(json.dumps(RemovalIntentLedger(sys.argv[1]).intents('orion')))")
        r = subprocess.run([sys.executable, "-c", script, str(self.path)], check=True,
                           capture_output=True, text=True, timeout=10)
        self.assertEqual(json.loads(r.stdout), [first])

    def test_same_request_changed_intent_never_overwrites_original(self):
        first = self.save()
        for changes in ({"target_key": "fixture:other"}, {"content_sha256": "b" * 64}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "different intent"):
                self.save(**changes)
        self.assertEqual(RemovalIntentLedger(self.path).intents("orion"), [first])

    def test_invalid_fields_do_not_append(self):
        for field, values in {
            "profile": [None, "", "unknown", True],
            "request_id": [None, " ", "x" * 201, "a\nb", "x\0"],
            "target_key": [False, "", "x" * 2001, "a\tb"],
            "content_sha256": [None, "a" * 63, "A" * 64, True],
        }.items():
            for value in values:
                with self.subTest(field=field, value=repr(value)[:30]), self.assertRaises(ValueError):
                    self.save(**{field: value})
        self.assertEqual(self.ledger.intents("orion"), [])
        with self.assertRaises(ValueError):
            self.ledger.intents("unknown")

    def test_profile_scope_and_original_memory_preservation(self):
        bank = MemoryBank(self.path)
        for p in ("orion", "aurora"):
            bank.add(p, "source_note", "Invented original", "fixture:original")
        before = {p: bank.export(p) for p in ("orion", "aurora")}
        first = self.save()
        other = self.save(profile="aurora", content_sha256="b" * 64)
        self.assertEqual(self.ledger.intents("orion"), [first])
        self.assertEqual(self.ledger.intents("aurora"), [other])
        self.assertEqual(self.ledger.intents("theo"), [])
        self.assertEqual({p: MemoryBank(self.path).export(p) for p in before}, before)

    def test_competing_changed_requests_commit_one_intent(self):
        barrier = Barrier(2)

        def write(digest):
            barrier.wait(timeout=10)
            try:
                return self.save(content_sha256=digest)
            except ValueError:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(write, ("a" * 64, "b" * 64)))
        self.assertEqual(results.count("conflict"), 1)
        saved = [r for r in results if isinstance(r, dict)]
        self.assertEqual(RemovalIntentLedger(self.path).intents("orion"), saved)

    def test_abrupt_exit_before_and_after_commit_in_both_journal_modes(self):
        for mode in ("DELETE", "WAL"):
            for committed in (False, True):
                with self.subTest(mode=mode, committed=committed):
                    path = Path(self.directory.name) / f"{mode}-{committed}.sqlite3"
                    ledger = RemovalIntentLedger(path)
                    original = ledger.record("aurora", "kept", target_key="fixture:kept",
                                             content_sha256="c" * 64)
                    with sqlite3.connect(path) as db:
                        self.assertEqual(db.execute("PRAGMA journal_mode=" + mode).fetchone()[0],
                                         mode.lower())
                    script = '''import os, sys
from contextlib import contextmanager
from lumina_orion.removal import RemovalIntentLedger
ledger = RemovalIntentLedger(sys.argv[1])
if sys.argv[2] == 'before':
    original_connect = ledger._connect
    @contextmanager
    def crash_connect():
        with original_connect() as db:
            db.set_trace_callback(lambda sql: os._exit(71) if sql == 'COMMIT' else None)
            yield db
    ledger._connect = crash_connect
ledger.record('orion','crash-test',target_key='fixture:crash',content_sha256='a'*64)
os._exit(72)
'''
                    r = subprocess.run([sys.executable, "-c", script, str(path),
                                        "after" if committed else "before"], capture_output=True,
                                       text=True, timeout=10)
                    self.assertEqual(r.returncode, 72 if committed else 71, r.stderr)
                    reopened = RemovalIntentLedger(path)
                    saved = reopened.intents("orion")
                    self.assertEqual(len(saved), int(committed))
                    retried = reopened.record("orion", "crash-test", target_key="fixture:crash",
                                              content_sha256="a" * 64)
                    if committed:
                        self.assertEqual(retried, saved[0])
                    self.assertEqual(reopened.intents("orion"), [retried])
                    self.assertEqual(reopened.intents("aurora"), [original])


if __name__ == "__main__":
    unittest.main()

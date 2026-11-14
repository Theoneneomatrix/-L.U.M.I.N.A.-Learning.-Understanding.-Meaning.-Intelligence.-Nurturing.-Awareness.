import copy
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lumina_orion.history_binding import (
    MAX_HISTORY_BYTES, SCHEME, bind_removal_history, require_history_binding,
)
from lumina_orion.memory import MemoryBank


def fixture(profile="orion"):
    return {"version": 1, "profile": profile, "history_boundary": "fixture:A",
            "requests": [{"version": 1, "profile": profile, "request_id": "fixture:r1",
                          "target_key": "fixture:target", "content_sha256": "a" * 64,
                          "state": "requested"}]}


def encoded(value):
    return json.dumps(value, ensure_ascii=False).encode()


class HistoryBindingTests(unittest.TestCase):
    def test_fixed_vector_empty_history_and_domain_separation(self):
        value = fixture(); value["requests"] = []
        preimage = (b'lumina-removal-history-sha256-v1\x00' +
                    b'{"history_boundary":"fixture:A","profile":"orion","requests":[],"version":1}')
        binding = bind_removal_history("orion", encoded(value))
        self.assertEqual(binding["history_sha256"], hashlib.sha256(preimage).hexdigest())
        self.assertEqual(set(binding), {"version", "scheme", "profile", "history_boundary", "history_sha256"})
        self.assertEqual(binding["scheme"], SCHEME)
        self.assertNotEqual(binding["history_sha256"], hashlib.sha256(preimage.split(b'\x00')[1]).hexdigest())

    def test_object_format_request_order_and_identity_normalization(self):
        value = fixture()
        value["requests"].append(dict(value["requests"][0], request_id="fixture:r2"))
        expected = bind_removal_history("orion", encoded(value))
        shuffled = dict(reversed(list(value.items())))
        shuffled["requests"] = list(reversed(copy.deepcopy(value["requests"])))
        shuffled["profile"] = " ORION "; shuffled["history_boundary"] = " fixture:A "
        for r in shuffled["requests"]:
            r["profile"] = " ORION "; r["request_id"] = ' '+r["request_id"]+' '
        self.assertEqual(bind_removal_history(" ORION ", json.dumps(shuffled, indent=4).encode()), expected)
        self.assertEqual(require_history_binding("orion", encoded(shuffled), expected), value)

    def test_changed_history_conflicts_under_same_boundary(self):
        value = fixture(); expected = bind_removal_history("orion", encoded(value))
        changes = []
        for field, replacement in [("request_id", "fixture:other"), ("target_key", "fixture:other"),
                                   ("content_sha256", "b" * 64)]:
            changed = copy.deepcopy(value); changed["requests"][0][field] = replacement; changes.append(changed)
        changes += [{**value, "requests": []}, {**value, "history_boundary": "fixture:B"}]
        changed = copy.deepcopy(value)
        changed["requests"].append(dict(changed["requests"][0], request_id="fixture:added")); changes.append(changed)
        for changed in changes:
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, "conflict"):
                require_history_binding("orion", encoded(changed), expected)

    def test_shape_profile_duplicate_and_bounded_identity_failures(self):
        bad = [[], None, {}, {**fixture(), "requests": [None] * 10001},
               {**fixture(), "history_boundary": None}, {**fixture(), "history_boundary": "x\ny"},
               {**fixture(), "history_boundary": "x" * 201}, {**fixture(), "source_text": "invented"}]
        for field in fixture():
            value = fixture(); del value[field]; bad.append(value)
        for field in fixture()["requests"][0]:
            value = fixture(); del value["requests"][0][field]; bad.append(value)
        for key, replacement in [("profile", "aurora"), ("state", "removed"), ("version", True),
                                 ("request_id", ""), ("target_key", "x" * 2001), ("content_sha256", "A" * 64)]:
            value = fixture(); value["requests"][0][key] = replacement; bad.append(value)
        value = fixture(); value["requests"].append(dict(value["requests"][0], request_id=" fixture:r1 ")); bad.append(value)
        for value in bad:
            with self.subTest(value_type=type(value)), self.assertRaises(ValueError):
                bind_removal_history("orion", encoded(value))

    def test_ambiguous_json_encoding_numbers_and_limits(self):
        base = encoded(fixture())
        bad = [None, base.decode(), bytearray(base), b'\xff', b'\xef\xbb\xbf'+base,
               base.decode().encode('utf-16'), b'{} {}', b'['*2000+b']'*2000,
               b' '*(MAX_HISTORY_BYTES+1), base.replace(b'fixture:A', b'fixture:\\ud800'),
               base.replace(b'"version": 1', b'"version":1,"ver\\u0073ion":1'),
               base.replace(b'"state": "requested"', b'"state":"requested","state":"requested"')]
        for number in (b'NaN', b'Infinity', b'1.0', b'1e0', b'true', b'2'):
            bad.append(base.replace(b'"version": 1', b'"version":'+number))
        for p in bad:
            with self.subTest(kind=type(p)), self.assertRaises(ValueError):
                bind_removal_history("orion", p)
        self.assertEqual(bind_removal_history("orion", base+b' '*(MAX_HISTORY_BYTES-len(base))),
                         bind_removal_history("orion", base))

    def test_unicode_and_detached_expected_outputs(self):
        value = fixture(); value["requests"][0]["target_key"] = "fixture:é😀"
        p = encoded(value); binding = bind_removal_history("orion", p); before = copy.deepcopy(binding)
        self.assertEqual(bind_removal_history("orion", json.dumps(value).encode()), binding)
        checked = require_history_binding("orion", p, binding)
        checked["requests"].clear()
        self.assertEqual(require_history_binding("orion", p, binding), value)
        self.assertEqual(binding, before)
        value["requests"][0]["target_key"] = "fixture:e\u0301😀"
        self.assertNotEqual(bind_removal_history("orion", encoded(value)), binding)
        for key, replacement in [("version", True), ("scheme", "other"), ("profile", "aurora"),
                                 ("history_boundary", "other"), ("history_sha256", "A"*64)]:
            with self.assertRaises(ValueError):
                require_history_binding("orion", p, dict(binding, **{key:replacement}))
        with self.assertRaises(ValueError):
            require_history_binding("orion", p, dict(binding, extra="invented"))

    def test_replacing_both_inputs_still_matches_not_current_authority(self):
        # Negative control: omitted requests plus a newly computed expected binding can pass.
        old = fixture(); old["requests"] = []
        checked = require_history_binding("orion", encoded(old), bind_removal_history("orion", encoded(old)))
        self.assertEqual(checked, old)
        self.assertNotIn("authority", checked)
        self.assertNotIn("reopening", checked)
        self.assertNotIn("current", checked)

    def test_all_profiles_restart_and_originals_preserved_delete_wal(self):
        script = '''import json,sys
from pathlib import Path
from lumina_orion.history_binding import require_history_binding
x=json.loads(Path(sys.argv[1]).read_text())
print(json.dumps(require_history_binding(sys.argv[2],json.dumps(x['history']).encode(),x['binding'])))
'''
        with tempfile.TemporaryDirectory() as directory:
            for mode in ("DELETE", "WAL"):
                root = Path(directory); db_path = root/(mode+'.sqlite3'); bank = MemoryBank(db_path)
                profiles = ('orion','aurora','theo','elysia','selene')
                for profile in profiles:
                    bank.add(profile, 'source_note', 'Invented original', 'fixture:original')
                with sqlite3.connect(db_path) as db:
                    db.execute('PRAGMA journal_mode='+mode)
                exports = {p:bank.export(p) for p in profiles}; before = db_path.read_bytes()
                for profile in profiles:
                    value = fixture(profile); binding = bind_removal_history(profile, encoded(value))
                    f = root/(mode+'-'+profile+'.json')
                    f.write_text(json.dumps(dict(history=value,binding=binding))); saved = f.read_bytes()
                    for _ in range(2):
                        r = subprocess.run([sys.executable,'-c',script,str(f),profile],check=True,
                                           capture_output=True,text=True,timeout=10)
                        self.assertEqual(json.loads(r.stdout),value)
                    self.assertEqual(f.read_bytes(),saved)
                    if profile!='orion':
                        with self.assertRaises(ValueError):
                            require_history_binding('orion',encoded(value),binding)
                self.assertEqual(db_path.read_bytes(),before)
                self.assertEqual({p:MemoryBank(db_path).export(p) for p in profiles},exports)


if __name__ == '__main__':
    unittest.main()

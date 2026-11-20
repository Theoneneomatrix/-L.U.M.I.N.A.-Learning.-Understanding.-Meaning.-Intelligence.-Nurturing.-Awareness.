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
from lumina_orion.capture_binding import bind_capture_notes, require_capture_note_binding
from lumina_orion.memory import PROFILES
from test_capture_consistency import read_all
from test_note_binding import note, seed


def capture(profile='orion'):
    return dict(version=1, profile=profile,
                memory_export=dict(format='lumina-memory-v1', profile=profile,
                                   records=[dict(note(), profile=profile)], results=[]),
                saved_intents=[], consistency='single_read_transaction', coverage='partial',
                freshness='not_assessed', authority='not_assessed')


def bind(value, identity='fixture:capture', profile='orion'):
    return bind_capture_notes(profile, value, capture_id=identity)


def require(value, expected, identity='fixture:capture', profile='orion'):
    return require_capture_note_binding(profile, value, expected, capture_id=identity)


class CaptureNoteBindingTests(unittest.TestCase):
    def test_literal_empty_vector_identity_scope_and_order(self):
        value = capture(); value['memory_export']['records'] = []
        encoded = b'{"capture_id":"fixture:empty","note_bindings":[],"profile":"orion","scheme":"lumina-capture-notes-sha256-v1","version":1}'
        expected = dict(version=1, scheme='lumina-capture-notes-sha256-v1', profile='orion',
                        capture_id='fixture:empty', note_bindings=[],
                        notes_sha256=hashlib.sha256(b'lumina-capture-notes-sha256-v1\x00'+encoded).hexdigest())
        self.assertEqual(bind(value, ' fixture:empty '), expected)
        self.assertEqual(require(value, expected, 'fixture:empty'), [])
        value = capture(); value['memory_export']['records'].append(dict(note(), id=2))
        expected = bind(value)
        value['memory_export']['records'].reverse()
        self.assertEqual(bind(value), expected)
        self.assertEqual([n['id'] for n in require(value, expected)], [1, 2])
        with self.assertRaises(ValueError):
            require(value, expected, 'fixture:another')
        for profile in PROFILES:
            self.assertNotEqual(bind(capture(profile), profile=profile)['notes_sha256'],
                                bind(capture(profile), 'fixture:another', profile)['notes_sha256'])

    def test_missing_extra_duplicate_changed_and_foreign_notes_rejected(self):
        original = capture(); expected = bind(original)
        variants = []
        x = capture(); x['memory_export']['records'] = []; variants.append(x)
        x = capture(); x['memory_export']['records'].append(dict(note(), id=2)); variants.append(x)
        x = capture(); x['memory_export']['records'] *= 2; variants.append(x)
        for field, replacement in [('id', 2), ('source', 'fixture:new'), ('profile', 'aurora')]:
            x = capture(); x['memory_export']['records'][0][field] = replacement; variants.append(x)
        x = capture('aurora'); variants.append(x)
        x = capture(); x['memory_export']['profile'] = 'aurora'; variants.append(x)
        for value in variants:
            before = copy.deepcopy(value)
            with self.assertRaises(ValueError):
                require(value, expected)
            self.assertEqual(value, before)
        for profile in PROFILES[1:]:
            with self.assertRaises(ValueError):
                require(capture(profile), expected, profile=profile)

    def test_malformed_envelopes_and_capture_shape_fail_closed(self):
        expected = bind(capture())
        bad = [None, {}, dict(expected, extra=True)]
        for field in expected:
            x = copy.deepcopy(expected); del x[field]; bad.append(x)
        for field, value in [('version', True), ('scheme', 'other'), ('profile', 'aurora'),
                             ('capture_id', '\ud800'), ('capture_id', ''),
                             ('notes_sha256', 'A'*64), ('note_bindings', None)]:
            bad.append(dict(expected, **{field: value}))
        x = copy.deepcopy(expected); x['note_bindings'] *= 2; bad.append(x)
        for field, value in [('version', True), ('note_id', True), ('profile', 'aurora'),
                             ('scheme', 'other'), ('note_sha256', 'A'*64)]:
            x = copy.deepcopy(expected); x['note_bindings'][0][field] = value; bad.append(x)
        for value in bad:
            with self.assertRaises(ValueError):
                require(capture(), value)
        for field, value in [('version', True), ('consistency', 'assumed'),
                             ('coverage', 'complete'), ('freshness', 'current'), ('authority', 'trusted')]:
            with self.assertRaises(ValueError):
                bind(dict(capture(), **{field: value}))
        for field in capture():
            x = capture(); del x[field]
            with self.assertRaises(ValueError): bind(x)
        for identity in (None, '', 'x'*201, 'bad\nidentity', '\ud800'):
            with self.assertRaises(ValueError): bind(capture(), identity)
        for profile in (None, '', 'other'):
            with self.assertRaises(ValueError): bind(capture(), profile=profile)

    def test_resource_limits_and_detached_values(self):
        value = capture(); expected = bind(value); before = copy.deepcopy(value)
        expected['note_bindings'][0]['note_id'] = 5
        self.assertEqual(value, before)
        expected = bind(value); rows = require(value, expected)
        rows[0]['derived_from'].append(2)
        self.assertEqual(value, before)
        self.assertEqual(bind(value), expected)
        with patch('lumina_orion.capture_binding.MAX_ENVELOPE_BYTES', 1):
            with self.assertRaises(ValueError): bind(value)
        for field in ('records', 'results'):
            x = capture(); x['memory_export'][field] = [None]*10001
            with self.assertRaises(ValueError): bind(x)
        x = capture(); x['saved_intents'] = [None]*10001
        with self.assertRaises(ValueError): bind(x)

    def test_unbound_payload_and_replaced_expected_negative_controls(self):
        value = capture(); expected = bind(value)
        changed = copy.deepcopy(value)
        changed['memory_export']['results'] = [{'fixture': 'different result'}]
        changed['saved_intents'] = [{'fixture': 'different request'}]
        # Deliberately not full-capture integrity: only notes are returned.
        self.assertEqual(bind(changed), expected)
        self.assertEqual(require(changed, expected), value['memory_export']['records'])
        changed['memory_export']['records'][0]['source'] = 'fixture:forged'
        with self.assertRaises(ValueError): require(changed, expected)
        forged = bind(changed)
        self.assertEqual(require(changed, forged), changed['memory_export']['records'])
        # Omission before both binding and comparison is undetectable.
        changed['memory_export']['records'] = []
        self.assertEqual(require(changed, bind(changed)), [])

    def test_all_profiles_fresh_process_comparison_preserves_originals(self):
        script = '''import json,sys
from lumina_orion.capture import capture_journal
from lumina_orion.capture_binding import require_capture_note_binding
print(json.dumps(require_capture_note_binding(sys.argv[2],capture_journal(sys.argv[1],sys.argv[2]),json.loads(sys.stdin.read()),capture_id="fixture:restart")))
'''
        with tempfile.TemporaryDirectory() as directory:
            for mode in ('DELETE', 'WAL'):
                path = Path(directory)/(mode+'.sqlite3'); seed(self, path, mode)
                before = read_all(path); original = path.read_bytes()
                for profile in PROFILES:
                    value = capture_journal(path, profile)
                    expected = bind(value, 'fixture:restart', profile)
                    for _ in range(2):
                        child = subprocess.run([sys.executable, '-c', script, str(path), profile],
                                               input=json.dumps(expected), capture_output=True,
                                               text=True, check=True, timeout=10)
                        self.assertEqual(json.loads(child.stdout), value['memory_export']['records'])
                    self.assertEqual(value, capture_journal(path, profile))
                self.assertEqual(read_all(path), before)
                self.assertEqual(path.read_bytes(), original)

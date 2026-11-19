import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lumina_orion.capture import capture_journal
from lumina_orion.memory import PROFILES
from lumina_orion.note_binding import bind_captured_note, require_note_binding
from test_capture import JournalCaptureTests
from test_capture_consistency import read_all

# Imported fixture builder only, not a second discovered test case.
seed = JournalCaptureTests.seed
del JournalCaptureTests


def note():
    return dict(id=1, profile='orion', kind='source_note', text='Invented é😀',
                source='fixture:source', created_at='fixture:time', supersedes=None,
                derived_from=[], needs_review=False, review_status='unverified')


class NoteBindingTests(unittest.TestCase):
    def test_vector_key_order_detachment_and_trust_negative_control(self):
        value = note()
        encoded = b'{"created_at":"fixture:time","derived_from":[],"id":1,"kind":"source_note","needs_review":false,"profile":"orion","review_status":"unverified","source":"fixture:source","supersedes":null,"text":"Invented \\u00e9\\ud83d\\ude00"}'
        expected = dict(version=1, scheme='lumina-captured-note-sha256-v1', profile='orion',
                        note_id=1, note_sha256=hashlib.sha256(
                            b'lumina-captured-note-sha256-v1\x00' + encoded).hexdigest())
        self.assertEqual(bind_captured_note(' ORION ', value), expected)
        self.assertEqual(bind_captured_note('orion', dict(reversed(list(value.items())))), expected)
        detached = require_note_binding('orion', value, expected)
        detached['derived_from'].append(2)
        self.assertEqual(value, note())
        # Forging both inputs still matches: integrity comparison is not authenticity.
        forged = dict(value, source='fixture:untrusted-assertion')
        self.assertEqual(require_note_binding('orion', forged, bind_captured_note('orion', forged)), forged)

    def test_each_bound_field_and_exact_unicode_changes_conflict(self):
        original = note(); expected = bind_captured_note('orion', original)
        changes = dict(id=2, profile='aurora', kind='question', text='Invented e\u0301😀',
                       source='fixture:other', created_at='fixture:other-time',
                       derived_from=[2], needs_review=True, review_status='superseded')
        for field, replacement in changes.items():
            value = dict(original, **{field: replacement})
            if field == 'needs_review':
                value['review_status'] = 'needs_review'
            with self.subTest(field=field), self.assertRaises(ValueError):
                require_note_binding('orion', value, expected)
        correction = dict(original, kind='correction', supersedes=2)
        bound = bind_captured_note('orion', correction)
        with self.assertRaises(ValueError):
            require_note_binding('orion', dict(correction, supersedes=3), bound)
        for text in (' Invented é😀', 'Invented é😀 ', 'Invented é😀\n'):
            with self.assertRaises(ValueError):
                require_note_binding('orion', dict(original, text=text), expected)
        parents = dict(original, derived_from=[2, 3])
        with self.assertRaises(ValueError):
            require_note_binding('orion', dict(parents, derived_from=[3, 2]), bind_captured_note('orion', parents))

    def test_reject_malformed_fields_without_echoing_content(self):
        bad = [None, {}, dict(note(), extra='secret')]
        for field in note():
            value = note(); del value[field]; bad.append(value)
        for field, value in [('id', True), ('id', 2**63), ('text', '\ud800'),
                             ('text', 'x'*20001), ('source', ''), ('created_at', None),
                             ('derived_from', [True]), ('derived_from', [2, 2]),
                             ('derived_from', [1]), ('derived_from', [2]*101),
                             ('needs_review', 1), ('review_status', 'approved'),
                             ('supersedes', True), ('kind', 'correction')]:
            bad.append(dict(note(), **{field: value}))
        for value in bad:
            before = copy.deepcopy(value)
            with self.assertRaises(ValueError) as raised:
                bind_captured_note('orion', value)
            self.assertNotIn('secret', str(raised.exception))
            self.assertEqual(value, before)
        bound = bind_captured_note('orion', note())
        for field, value in [('version', True), ('scheme', 'other'), ('note_id', True),
                             ('profile', 'aurora'), ('note_sha256', 'A'*64)]:
            with self.assertRaises(ValueError):
                require_note_binding('orion', note(), dict(bound, **{field: value}))

    def test_byte_bound_and_profile_separation(self):
        value = dict(note(), text='😀'*20000, source='😀'*2000)
        with self.assertRaises(ValueError):
            bind_captured_note('orion', value)
        bindings = [bind_captured_note(p, dict(note(), profile=p)) for p in PROFILES]
        self.assertEqual(len({b['note_sha256'] for b in bindings}), len(PROFILES))
        for value in (None, '', 'other', True):
            with self.assertRaises(ValueError):
                bind_captured_note(value, note())

    def test_capture_restart_all_profiles_preserves_originals_and_v1_text_digests(self):
        script = '''import json,sys
from lumina_orion.capture import capture_journal
from lumina_orion.note_binding import require_note_binding
saved=json.loads(sys.stdin.read())
rows=capture_journal(sys.argv[1],sys.argv[2])["memory_export"]["records"]
print(json.dumps([require_note_binding(sys.argv[2],r,b) for r,b in zip(rows,saved)]))
'''
        with tempfile.TemporaryDirectory() as directory:
            for mode in ('DELETE', 'WAL'):
                path = Path(directory) / (mode + '.sqlite3')
                seed(self, path, mode)
                before = read_all(path); original = path.read_bytes()
                for profile in PROFILES:
                    capture = capture_journal(path, profile)
                    rows = capture['memory_export']['records']
                    bound = [bind_captured_note(profile, row) for row in rows]
                    for _ in range(2):
                        child = subprocess.run([sys.executable, '-c', script, str(path), profile],
                                               input=json.dumps(bound), text=True, capture_output=True,
                                               check=True, timeout=10)
                        self.assertEqual(json.loads(child.stdout), rows)
                    text_digest = hashlib.sha256(rows[0]['text'].encode()).hexdigest()
                    self.assertEqual(capture['saved_intents'][0]['content_sha256'], text_digest)
                    # Metadata-only edit is invisible to v1 text hashing but detected here.
                    changed = dict(rows[0], source='fixture:changed')
                    self.assertEqual(hashlib.sha256(changed['text'].encode()).hexdigest(), text_digest)
                    with self.assertRaises(ValueError):
                        require_note_binding(profile, changed, bound[0])
                self.assertEqual(read_all(path), before)
                self.assertEqual(path.read_bytes(), original)

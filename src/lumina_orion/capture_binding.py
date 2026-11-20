"""Pure association of a declared capture identity with its supplied note set."""
from __future__ import annotations

import hashlib
import json

from .note_binding import SCHEME as NOTE_SCHEME, _id, bind_captured_note, require_note_binding
from .removal import _identity, _profile
from .restore import _digest
from .snapshot import _fields

SCHEME = "lumina-capture-notes-sha256-v1"
MAX_ENVELOPE_BYTES = 1024 * 1024


def _rows(value):
    if type(value) is not list or len(value) > 10000:
        raise ValueError("Capture rows must be a bounded list")
    return value


def _envelope(profile, capture_id, bindings):
    capture_id = _identity(capture_id, 200)
    try:
        capture_id.encode("utf-8", errors="strict")
    except UnicodeError:
        raise ValueError("Invalid capture identity Unicode") from None
    copied, seen = [], set()
    for binding in _rows(bindings):
        _fields(binding, ("version", "scheme", "profile", "note_id", "note_sha256"))
        if (type(binding["version"]) is not int or binding["version"] != 1
                or binding["scheme"] != NOTE_SCHEME or binding["profile"] != profile):
            raise ValueError("Invalid or foreign-profile note binding")
        _id(binding["note_id"])
        _digest(binding["note_sha256"])
        if binding["note_id"] in seen:
            raise ValueError("Duplicate note association")
        seen.add(binding["note_id"])
        copied.append(dict(binding))
    copied.sort(key=lambda b: b["note_id"])
    envelope = dict(version=1, scheme=SCHEME, profile=profile,
                    capture_id=capture_id, note_bindings=copied)
    payload = json.dumps(envelope, sort_keys=True, ensure_ascii=True,
                         separators=(",", ":"), allow_nan=False).encode("ascii")
    if len(payload) > MAX_ENVELOPE_BYTES:
        raise ValueError("Capture note envelope exceeds byte limit")
    envelope["notes_sha256"] = hashlib.sha256(SCHEME.encode("ascii") + b"\x00" + payload).hexdigest()
    return envelope


def _notes(profile, capture):
    _fields(capture, ("version", "profile", "memory_export", "saved_intents",
                      "consistency", "coverage", "freshness", "authority"))
    if (type(capture["version"]) is not int or capture["version"] != 1
            or capture["profile"] != profile
            or capture["consistency"] != "single_read_transaction"
            or capture["coverage"] != "partial"
            or capture["freshness"] != "not_assessed"
            or capture["authority"] != "not_assessed"):
        raise ValueError("Unsupported capture declaration")
    export = capture["memory_export"]
    _fields(export, ("format", "profile", "records", "results"))
    if export["format"] != "lumina-memory-v1" or export["profile"] != profile:
        raise ValueError("Capture export format or profile mismatch")
    _rows(export["results"])  # Contents are outside this note-only binding.
    _rows(capture["saved_intents"])
    return _rows(export["records"])


def bind_capture_notes(profile: str, capture: dict, *, capture_id: str) -> dict:
    """Return a detached envelope for exactly the supplied note set, with no I/O.

    Capture identity is caller-declared, trimmed and scoped by profile. Note
    ordering is ignored; duplicate IDs and foreign profiles fail. This does not
    bind results/requests, prove a common snapshot or authenticate any assertion.
    """
    profile = _profile(profile)
    rows = _notes(profile, capture)
    return _envelope(profile, capture_id, [bind_captured_note(profile, row) for row in rows])


def require_capture_note_binding(profile: str, capture: dict, expected: dict, *,
                                 capture_id: str) -> list[dict]:
    """Return detached notes sorted by ID matching an independently saved envelope.

    No capture metadata, results or requests are returned as verified. Replacing
    both candidate and expected envelope bypasses comparison. No action is taken.
    """
    profile = _profile(profile)
    _fields(expected, ("version", "scheme", "profile", "capture_id", "note_bindings", "notes_sha256"))
    if (type(expected["version"]) is not int or expected["version"] != 1
            or expected["scheme"] != SCHEME or expected["profile"] != profile):
        raise ValueError("Invalid expected capture note envelope")
    _digest(expected["notes_sha256"])
    if _envelope(profile, expected["capture_id"], expected["note_bindings"]) != expected:
        raise ValueError("Invalid expected capture note envelope")
    actual = bind_capture_notes(profile, capture, capture_id=capture_id)
    if actual != expected:
        raise ValueError("Capture note binding conflict")
    rows = sorted(_notes(profile, capture), key=lambda row: row["id"])
    return [require_note_binding(profile, row, binding)
            for row, binding in zip(rows, actual["note_bindings"])]

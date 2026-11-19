"""Bind supplied captured note fields without authenticating their assertions."""
from __future__ import annotations

import hashlib
import json

from .memory import KINDS
from .removal import _profile
from .restore import _digest
from .snapshot import _fields

SCHEME = "lumina-captured-note-sha256-v1"
FIELDS = ("id", "profile", "kind", "text", "source", "created_at", "supersedes",
          "derived_from", "needs_review", "review_status")
MAX_NOTE_BYTES = 256 * 1024


def _id(value):
    if type(value) is not int or not 0 < value <= 2**63 - 1:
        raise ValueError("Note ID must be a positive SQLite integer")


def _note(profile, note):
    _fields(note, FIELDS)
    if note["profile"] != _profile(profile):
        raise ValueError("Captured note profile mismatch")
    _id(note["id"])
    for field, limit in (("text", 20000), ("source", 2000), ("created_at", 200)):
        value = note[field]
        if type(value) is not str or not value.strip() or len(value) > limit:
            raise ValueError("Invalid captured note text field")
        try:
            value.encode("utf-8", errors="strict")
        except UnicodeError:
            raise ValueError("Invalid captured note Unicode") from None
    if type(note["kind"]) is not str or note["kind"] not in KINDS:
        raise ValueError("Invalid captured note kind")
    if note["supersedes"] is not None:
        _id(note["supersedes"])
    if (note["kind"] == "correction") != (note["supersedes"] is not None):
        raise ValueError("Invalid captured correction link")
    parents = note["derived_from"]
    if type(parents) is not list or len(parents) > 100:
        raise ValueError("Invalid captured dependencies")
    for parent in parents:
        _id(parent)
    if len(set(parents)) != len(parents) or note["id"] in parents:
        raise ValueError("Invalid captured dependency identity")
    if (type(note["needs_review"]) is not bool
            or type(note["review_status"]) is not str
            or note["review_status"] not in ("unverified", "needs_review", "superseded")
            or (note["review_status"] != "superseded"
                and note["needs_review"] != (note["review_status"] == "needs_review"))):
        raise ValueError("Invalid captured review markers")
    # Fixed shallow schema: copy the only mutable field before encoding/returning.
    result = {field: note[field] for field in FIELDS}
    result["derived_from"] = list(parents)
    return result


def _binding(note):
    payload = json.dumps(note, sort_keys=True, ensure_ascii=True,
                         separators=(",", ":"), allow_nan=False).encode("ascii")
    if len(payload) > MAX_NOTE_BYTES:
        raise ValueError("Captured note exceeds binding byte limit")
    return dict(version=1, scheme=SCHEME, profile=note["profile"], note_id=note["id"],
                note_sha256=hashlib.sha256(SCHEME.encode("ascii") + b"\x00" + payload).hexdigest())


def bind_captured_note(profile: str, note: dict) -> dict:
    """Bind exactly FIELDS from one supplied note; perform no I/O.

    Whitespace, Unicode scalar sequences, timestamps and dependency order are
    preserved. This separate scheme does not change text-only removal digests.
    A digest is not authentication, freshness, graph validation or authority.
    """
    return _binding(_note(profile, note))


def require_note_binding(profile: str, note: dict, expected: dict) -> dict:
    """Return a detached note matching an independently retained binding.

    Replacing both note and expected binding bypasses comparison. No target
    mapping, provenance verification, retention or action permission is added.
    """
    _fields(expected, ("version", "scheme", "profile", "note_id", "note_sha256"))
    if (type(expected["version"]) is not int or expected["version"] != 1
            or expected["scheme"] != SCHEME or expected["profile"] != _profile(profile)):
        raise ValueError("Invalid expected note binding")
    _id(expected["note_id"])
    _digest(expected["note_sha256"])
    value = _note(profile, note)
    if _binding(value) != expected:
        raise ValueError("Captured note binding conflict")
    return value

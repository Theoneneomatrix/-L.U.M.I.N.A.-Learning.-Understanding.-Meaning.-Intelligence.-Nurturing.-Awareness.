"""Pure binding of captured removal requests, never completion or authority."""
from __future__ import annotations

import hashlib
import json

from .capture_binding import _notes, _rows
from .note_binding import _id
from .removal import _identity, _profile
from .restore import _digest
from .snapshot import _fields

SCHEME = "lumina-capture-requests-sha256-v1"
FIELDS = ("id", "version", "profile", "request_id", "target_key",
          "content_sha256", "created_at", "state")
MAX_ENVELOPE_BYTES = 1024 * 1024


def _scalar(value, limit):
    if type(value) is not str or not value.strip() or len(value) > limit:
        raise ValueError("Invalid captured request text")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError:
        raise ValueError("Invalid captured request Unicode") from None
    return value


def _requests(profile, rows):
    copied, ids, requests = [], set(), set()
    for row in _rows(rows):
        _fields(row, FIELDS)
        _id(row["id"])
        if (type(row["version"]) is not int or row["version"] != 1
                or row["profile"] != profile or row["state"] != "requested"):
            raise ValueError("Invalid or foreign-profile captured request")
        for field, limit in (("request_id", 200), ("target_key", 2000)):
            _scalar(row[field], limit)
            if _identity(row[field], limit) != row[field]:
                raise ValueError("Captured request identity must be canonical")
        _scalar(row["created_at"], 200)  # Opaque recorded text, not a clock.
        _digest(row["content_sha256"])
        if row["id"] in ids or row["request_id"] in requests:
            raise ValueError("Duplicate captured request identity")
        ids.add(row["id"])
        requests.add(row["request_id"])
        copied.append({field: row[field] for field in FIELDS})
    return sorted(copied, key=lambda row: row["id"])


def _envelope(profile, capture_id, rows):
    capture_id = _scalar(_identity(capture_id, 200), 200)
    envelope = dict(version=1, scheme=SCHEME, profile=profile,
                    capture_id=capture_id, requests=_requests(profile, rows))
    payload = json.dumps(envelope, sort_keys=True, ensure_ascii=True,
                         separators=(",", ":"), allow_nan=False).encode("ascii")
    if len(payload) > MAX_ENVELOPE_BYTES:
        raise ValueError("Capture request envelope exceeds byte limit")
    envelope["requests_sha256"] = hashlib.sha256(
        SCHEME.encode("ascii") + b"\x00" + payload).hexdigest()
    return envelope


def bind_capture_requests(profile: str, capture: dict, *, capture_id: str) -> dict:
    """Bind exactly the supplied request rows and declared capture ID, with no I/O.

    Local IDs, timestamps and target identities remain assertions. Original v1
    content_sha256 stays text-only. Notes/results are not bound or verified.
    This is not proof of removal, complete coverage, freshness or permission.
    """
    profile = _profile(profile)
    _notes(profile, capture)  # Shared v1 capture shape; note contents stay unbound.
    return _envelope(profile, capture_id, capture["saved_intents"])


def require_capture_request_binding(profile: str, capture: dict, expected: dict, *,
                                    capture_id: str) -> list[dict]:
    """Return detached requests sorted by local ID matching a retained envelope.

    Expected must be retained independently. Replacing both inputs or omitting
    rows before creating both inputs bypasses comparison. No action is taken.
    """
    profile = _profile(profile)
    _fields(expected, ("version", "scheme", "profile", "capture_id",
                       "requests", "requests_sha256"))
    if (type(expected["version"]) is not int or expected["version"] != 1
            or expected["scheme"] != SCHEME or expected["profile"] != profile):
        raise ValueError("Invalid expected capture request envelope")
    _digest(expected["requests_sha256"])
    if _envelope(profile, expected["capture_id"], expected["requests"]) != expected:
        raise ValueError("Invalid expected capture request envelope")
    actual = bind_capture_requests(profile, capture, capture_id=capture_id)
    if actual != expected:
        raise ValueError("Capture request binding conflict")
    return actual["requests"]

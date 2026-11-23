"""Pure integrity comparison for supplied captured research-result assertions."""
from __future__ import annotations

import hashlib
import json

from .capture_binding import _notes, _rows
from .memory import _manifest_json
from .removal import _identity, _profile
from .restore import _digest
from .snapshot import _fields

SCHEME = "lumina-capture-results-sha256-v1"
FIELDS = ("profile", "run_id", "step", "manifest_sha256", "result_text",
          "created_at", "manifest")
MAX_ENVELOPE_BYTES = 1024 * 1024


def _scalar(value, limit):
    if type(value) is not str or not value.strip() or len(value) > limit:
        raise ValueError("Invalid captured result text")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError:
        raise ValueError("Invalid captured result Unicode") from None
    return value


def _results(profile, rows):
    copied, seen = [], set()
    for row in _rows(rows):
        _fields(row, FIELDS)
        if row["profile"] != profile:
            raise ValueError("Foreign-profile captured result")
        for field, limit in (("run_id", 200), ("step", 200),
                             ("result_text", 20000), ("created_at", 200)):
            _scalar(row[field], limit)  # Exact opaque text; no trimming or clock.
        _digest(row["manifest_sha256"])
        try:
            encoded = _manifest_json(row["manifest"])
        except (ValueError, UnicodeError):
            raise ValueError("Invalid captured result manifest") from None
        if hashlib.sha256(encoded.encode("utf-8")).hexdigest() != row["manifest_sha256"]:
            raise ValueError("Captured result manifest digest mismatch")
        key = (row["run_id"], row["step"])
        if key in seen:
            raise ValueError("Duplicate captured result identity")
        seen.add(key)
        detached = {field: row[field] for field in FIELDS}
        detached["manifest"] = json.loads(encoded)
        copied.append(detached)
    return sorted(copied, key=lambda row: (row["run_id"], row["step"]))


def _envelope(profile, capture_id, rows):
    capture_id = _scalar(_identity(capture_id, 200), 200)
    envelope = dict(version=1, scheme=SCHEME, profile=profile,
                    capture_id=capture_id, results=_results(profile, rows))
    payload = json.dumps(envelope, sort_keys=True, ensure_ascii=True,
                         separators=(",", ":"), allow_nan=False).encode("ascii")
    if len(payload) > MAX_ENVELOPE_BYTES:
        raise ValueError("Capture result envelope exceeds byte limit")
    envelope["results_sha256"] = hashlib.sha256(
        SCHEME.encode("ascii") + b"\x00" + payload).hexdigest()
    return envelope


def bind_capture_results(profile: str, capture: dict, *, capture_id: str) -> dict:
    """Bind exact supplied result rows and declared profile/capture ID, with no I/O.

    Reuses the existing v1 input-manifest validation and digest unchanged. Notes
    and requests are unbound. A match is not fresh, authorized result reuse,
    authentic provenance, complete coverage or proof of a common snapshot.
    """
    profile = _profile(profile)
    _notes(profile, capture)  # Validate shared v1 shape, not note/request contents.
    return _envelope(profile, capture_id, capture["memory_export"]["results"])


def require_capture_result_binding(profile: str, capture: dict, expected: dict, *,
                                   capture_id: str) -> list[dict]:
    """Return detached results sorted by run/step matching a retained envelope.

    Expected must be independently retained. Replacing both inputs or omitting
    rows before creating both bypasses comparison. This performs no action.
    """
    profile = _profile(profile)
    _fields(expected, ("version", "scheme", "profile", "capture_id",
                       "results", "results_sha256"))
    if (type(expected["version"]) is not int or expected["version"] != 1
            or expected["scheme"] != SCHEME or expected["profile"] != profile):
        raise ValueError("Invalid expected capture result envelope")
    _digest(expected["results_sha256"])
    if _envelope(profile, expected["capture_id"], expected["results"]) != expected:
        raise ValueError("Invalid expected capture result envelope")
    actual = bind_capture_results(profile, capture, capture_id=capture_id)
    if actual != expected:
        raise ValueError("Capture result binding conflict")
    return actual["results"]

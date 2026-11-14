"""Bind declared removal history content, never its completeness or authority."""
from __future__ import annotations

import hashlib
import json

from .removal import _identity, _profile
from .restore import _digest
from .snapshot import _fields
from .snapshot_binding import _reject_number, _unicode_scalars, _unique_object

MAX_HISTORY_BYTES = 1024 * 1024
SCHEME = "lumina-removal-history-sha256-v1"
_DOMAIN = SCHEME.encode("ascii") + b"\x00"


def _parse(profile, payload):
    profile = _profile(profile)
    if type(payload) is not bytes or len(payload) > MAX_HISTORY_BYTES:
        raise ValueError("History must be UTF-8 bytes of at most 1 MiB")
    try:
        raw = json.loads(payload.decode("utf-8", errors="strict"),
                         object_pairs_hook=_unique_object,
                         parse_float=_reject_number, parse_constant=_reject_number)
        _unicode_scalars(raw)
    except (ValueError, RecursionError):
        raise ValueError("Invalid or ambiguous history JSON") from None
    _fields(raw, ("version", "profile", "history_boundary", "requests"))
    if type(raw["version"]) is not int or raw["version"] != 1:
        raise ValueError("Unsupported history version")
    if _profile(raw["profile"]) != profile:
        raise ValueError("History profile mismatch")
    boundary = _identity(raw["history_boundary"], 200)
    if type(raw["requests"]) is not list or len(raw["requests"]) > 10000:
        raise ValueError("History requests must be a bounded list")
    requests, seen = [], set()
    for row in raw["requests"]:
        _fields(row, ("version", "profile", "request_id", "target_key",
                      "content_sha256", "state"))
        if type(row["version"]) is not int or row["version"] != 1:
            raise ValueError("Unsupported request version")
        if _profile(row["profile"]) != profile or row["state"] != "requested":
            raise ValueError("Request profile or state mismatch")
        request_id = _identity(row["request_id"], 200)
        if request_id in seen:
            raise ValueError("Duplicate history request identity")
        seen.add(request_id)
        requests.append({"version": 1, "profile": profile, "request_id": request_id,
                         "target_key": _identity(row["target_key"], 2000),
                         "content_sha256": _digest(row["content_sha256"]),
                         "state": "requested"})
    requests.sort(key=lambda r: r["request_id"])
    return {"version": 1, "profile": profile, "history_boundary": boundary,
            "requests": requests}


def _binding(history):
    encoded = json.dumps(history, sort_keys=True, ensure_ascii=True,
                         separators=(",", ":"), allow_nan=False).encode("ascii")
    return {"version": 1, "scheme": SCHEME, "profile": history["profile"],
            "history_boundary": history["history_boundary"],
            "history_sha256": hashlib.sha256(_DOMAIN + encoded).hexdigest()}


def bind_removal_history(profile: str, payload: bytes) -> dict:
    """Return a detached binding to a strict, normalized declaration; no I/O.

    Requests are a set keyed by normalized request ID, sorted for hashing.
    Sorting is not chronology. Omission/completeness, source truth, freshness
    and current authority cannot be established by this content identity.
    """
    return _binding(_parse(profile, payload))


def require_history_binding(profile: str, payload: bytes, expected: dict) -> dict:
    """Return detached declared history only on a separately selected match.

    Replacing both inputs defeats comparison. No persistence, source lookup,
    actual ledger extraction, history observation upgrade, or action occurs.
    """
    _fields(expected, ("version", "scheme", "profile", "history_boundary", "history_sha256"))
    if (type(expected["version"]) is not int or expected["version"] != 1
            or expected["scheme"] != SCHEME or expected["profile"] != _profile(profile)):
        raise ValueError("Invalid expected history binding")
    _digest(expected["history_sha256"])
    history = _parse(profile, payload)
    if _binding(history) != expected:
        raise ValueError("History binding conflict")
    return history

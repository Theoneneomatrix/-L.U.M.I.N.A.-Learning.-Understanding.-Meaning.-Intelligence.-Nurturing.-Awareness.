"""Bind normalized manifest declarations; never authenticate or permit a restore."""
from __future__ import annotations

import hashlib
import json

from .removal import _profile
from .restore import _digest
from .snapshot import _fields, validate_snapshot_manifest

MAX_MANIFEST_BYTES = 1024 * 1024
SCHEME = "lumina-manifest-sha256-v1"
_DOMAIN = b"lumina-manifest-sha256-v1\x00"


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON member")
        result[key] = value
    return result


def _reject_number(value):
    raise ValueError("Only integer manifest numbers are supported")


def _unicode_scalars(value):
    if type(value) is str:
        value.encode("utf-8", errors="strict")
    elif type(value) is dict:
        for key, item in value.items():
            _unicode_scalars(key)
            _unicode_scalars(item)
    elif type(value) is list:
        for item in value:
            _unicode_scalars(item)


def _parse(profile, payload):
    if type(payload) is not bytes or len(payload) > MAX_MANIFEST_BYTES:
        raise ValueError("Manifest must be UTF-8 bytes of at most 1 MiB")
    try:
        raw = json.loads(payload.decode("utf-8", errors="strict"),
                         object_pairs_hook=_unique_object,
                         parse_float=_reject_number, parse_constant=_reject_number)
        _unicode_scalars(raw)
    except (ValueError, RecursionError):
        # Do not include source fragments or JSONDecodeError.doc in the error.
        raise ValueError("Invalid or ambiguous manifest JSON") from None
    return validate_snapshot_manifest(profile, raw)


def _binding(manifest):
    encoded = json.dumps(manifest, sort_keys=True, ensure_ascii=True,
                         separators=(",", ":"), allow_nan=False).encode("ascii")
    return {"version": 1, "scheme": SCHEME, "profile": manifest["profile"],
            "snapshot_id": manifest["snapshot_id"],
            "manifest_sha256": hashlib.sha256(_DOMAIN + encoded).hexdigest()}


def bind_snapshot_manifest(profile: str, payload: bytes) -> dict:
    """Return a deterministic binding for a strict JSON declaration.

    Identity trimming/profile normalization precedes hashing; array order and
    Unicode scalar sequences are preserved. This is not a raw-file checksum,
    JCS, an immutable registry, or a verification of the listed content hashes.
    """
    return _binding(_parse(profile, payload))


def require_snapshot_binding(profile: str, payload: bytes, expected: dict) -> dict:
    """Return a detached manifest only if it matches a separately saved binding.

    The caller must preserve and select the expected binding independently.
    Replacing both manifest and expected value bypasses this comparison. A
    match says nothing about freshness, coverage, authority, or safe reopening.
    """
    _fields(expected, ("version", "scheme", "profile", "snapshot_id", "manifest_sha256"))
    if (type(expected["version"]) is not int or expected["version"] != 1
            or expected["scheme"] != SCHEME or expected["profile"] != _profile(profile)):
        raise ValueError("Invalid expected binding")
    _digest(expected["manifest_sha256"])
    manifest = _parse(profile, payload)
    if _binding(manifest) != expected:
        raise ValueError("Snapshot binding conflict")
    return manifest

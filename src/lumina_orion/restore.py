"""Read-only comparison of supplied removal requests and restored record mappings.

No filesystem, database, network, removal, authorization, or gate operation occurs.
Input identity/digest assertions and coverage are supplied by the caller.
"""
from __future__ import annotations

import re

from .removal import _identity, _profile


def _digest(value):
    if type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("Content digest must be lowercase SHA-256 hex")
    return value


def _rows(value):
    if type(value) is not list or any(type(row) is not dict for row in value):
        raise ValueError("Mappings must be lists of dictionaries")
    return value


def inspect_restore(profile: str, requests: list[dict],
                    restored_records: list[dict]) -> dict:
    """Compare explicit profile/target/version mappings without mutating inputs.

    A unique match means the requested content version is LISTED AS PRESENT,
    not removed. Missing matches, empty requests, or another version never
    establish permission, complete coverage, current authority, or safe reopening.
    """
    profile = _profile(profile)
    selected = []
    request_ids = set()
    for row in _rows(requests):
        if _profile(row.get("profile")) != profile:
            continue
        if type(row.get("version")) is not int or row["version"] != 1:
            raise ValueError("Unsupported removal request version")
        if row.get("state") != "requested":
            raise ValueError("Unsupported removal request state")
        request_id = _identity(row.get("request_id"), 200)
        if request_id in request_ids:
            raise ValueError("Duplicate removal request identity")
        request_ids.add(request_id)
        selected.append((request_id, _identity(row.get("target_key"), 2000),
                         _digest(row.get("content_sha256"))))

    by_target = {}
    record_ids = set()
    for row in _rows(restored_records):
        if _profile(row.get("profile")) != profile:
            continue
        record_key = _identity(row.get("record_key"), 2000)
        if record_key in record_ids:
            raise ValueError("Duplicate restored record identity")
        record_ids.add(record_key)
        target = _identity(row.get("target_key"), 2000)
        digest = _digest(row.get("content_sha256"))
        by_target.setdefault(target, []).append((record_key, digest))

    findings = []
    for request_id, target, digest in selected:
        candidates = by_target.get(target, [])
        exact = sorted(key for key, version in candidates if version == digest)
        if len(exact) > 1:
            status = "ambiguous_match"
        elif exact:
            status = "requested_version_present"
        elif candidates:
            status = "different_version_listed"
        else:
            status = "target_not_listed"
        findings.append({"request_id": request_id, "status": status,
                         "matching_record_keys": exact})

    return {"version": 1, "profile": profile, "read_only": True,
            "coverage": "caller_supplied_only", "reopening": "not_assessed",
            "findings": findings}

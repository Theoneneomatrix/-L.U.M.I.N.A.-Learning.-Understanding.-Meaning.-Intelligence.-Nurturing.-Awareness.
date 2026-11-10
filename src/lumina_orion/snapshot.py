"""Validate declared snapshot metadata, never freshness or restore permission."""
from __future__ import annotations

from .removal import _identity, _profile
from .restore import _digest, inspect_restore


def _fields(value, names):
    if type(value) is not dict or set(value) != set(names):
        raise ValueError("Unexpected or missing manifest fields")


def validate_snapshot_manifest(profile: str, manifest: dict) -> dict:
    """Return a detached normalized v1 declaration, or raise ValueError.

    Schema validation is not authentication, content hashing, completeness,
    current-history verification, removal, or authorization. Null boundary
    explicitly means unspecified. No file/database/network operation occurs.
    """
    profile = _profile(profile)
    _fields(manifest, ("version", "profile", "snapshot_id",
                       "removal_history_boundary", "coverage", "records"))
    if type(manifest["version"]) is not int or manifest["version"] != 1:
        raise ValueError("Unsupported manifest version")
    if _profile(manifest["profile"]) != profile:
        raise ValueError("Snapshot profile mismatch")
    snapshot_id = _identity(manifest["snapshot_id"], 200)
    boundary = manifest["removal_history_boundary"]
    if boundary is not None:
        boundary = _identity(boundary, 200)
    coverage = manifest["coverage"]
    _fields(coverage, ("scope", "declaration", "exclusions"))
    if coverage["scope"] != "profile_records":
        raise ValueError("Unsupported coverage scope")
    declaration = coverage["declaration"]
    if type(declaration) is not str or declaration not in ("partial", "declared_complete"):
        raise ValueError("Unsupported coverage declaration")
    excluded = coverage["exclusions"]
    if type(excluded) is not list or len(excluded) > 1000:
        raise ValueError("Exclusions must be a bounded list")
    excluded = [_identity(value, 2000) for value in excluded]
    if len(set(excluded)) != len(excluded):
        raise ValueError("Duplicate exclusion identity")
    if declaration == "declared_complete" and excluded:
        raise ValueError("Complete declaration cannot list exclusions")
    rows = manifest["records"]
    if type(rows) is not list or len(rows) > 10000:
        raise ValueError("Records must be a bounded list")
    records = []
    for row in rows:
        _fields(row, ("profile", "record_key", "target_key", "content_sha256"))
        if _profile(row["profile"]) != profile:
            raise ValueError("Record profile mismatch")
        records.append({"profile": profile,
                        "record_key": _identity(row["record_key"], 2000),
                        "target_key": _identity(row["target_key"], 2000),
                        "content_sha256": _digest(row["content_sha256"])})
    # Reuse the existing duplicate-record check without changing any data.
    inspect_restore(profile, [], records)
    return {"version": 1, "profile": profile, "snapshot_id": snapshot_id,
            "removal_history_boundary": boundary,
            "coverage": {"scope": "profile_records", "declaration": declaration,
                         "exclusions": excluded}, "records": records}

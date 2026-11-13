"""Read-only history assertion comparison, never authority or restore clearance."""
from __future__ import annotations

from .removal import _identity, _profile
from .snapshot import _fields
from .snapshot_binding import require_snapshot_binding


def inspect_snapshot_history(profile: str, payload: bytes, expected: dict,
                             observation: dict | None = None) -> dict:
    """Compare a bound declaration with a separately supplied v1 observation.

    A caller must obtain/select the observation independently. Even a `current`
    label is unauthenticated input. This function cannot detect replay when both
    inputs roll back together. No I/O, retention, gate opening or action occurs.
    Malformed/conflicting binding inputs raise ValueError, never a permissive
    report. All reports explicitly leave reopening unassessed.
    """
    manifest = require_snapshot_binding(profile, payload, expected)
    profile = manifest["profile"]
    declared = manifest["removal_history_boundary"]
    if observation is None:
        status = "observation_missing"
    else:
        _fields(observation, ("version", "profile", "state", "history_boundary"))
        if type(observation["version"]) is not int or observation["version"] != 1:
            raise ValueError("Unsupported history observation version")
        if _profile(observation["profile"]) != profile:
            raise ValueError("History observation profile mismatch")
        state = observation["state"]
        if type(state) is not str or state not in ("current", "stale", "unavailable"):
            raise ValueError("Unsupported history observation state")
        boundary = observation["history_boundary"]
        if boundary is not None:
            boundary = _identity(boundary, 200)
        if state == "current" and boundary is None:
            raise ValueError("Current assertion requires a history boundary")
        if state != "current":
            status = "observation_" + state
        elif declared is None:
            status = "declaration_boundary_unspecified"
        elif declared != boundary:
            status = "boundary_conflict"
        else:
            status = "boundary_matches_assertion"
    return {"version": 1, "profile": profile, "snapshot_id": manifest["snapshot_id"],
            "read_only": True, "history_status": status,
            "authority": "caller_asserted_only", "reopening": "not_assessed"}

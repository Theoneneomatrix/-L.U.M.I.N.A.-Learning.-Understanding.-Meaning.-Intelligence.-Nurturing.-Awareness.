"""Compose bound declarations for inspection, never for restore permission."""
from __future__ import annotations

from .history_binding import require_history_binding
from .restore import inspect_restore
from .snapshot_binding import require_snapshot_binding


def inspect_bound_restore(profile: str, snapshot_payload: bytes,
                          expected_snapshot: dict, *,
                          history_payload: bytes | None = None,
                          expected_history: dict | None = None) -> dict:
    """Compare separately bound snapshot/history declarations without I/O.

    Expected bindings must be selected separately by the caller. Both history
    arguments are required together; missing history or an unspecified snapshot
    boundary leaves findings unresolved (None, not an empty comparison). Invalid
    bindings, profiles or conflicting boundary labels raise ValueError. Even a
    compared report establishes only caller-declared content, never independent
    provenance, completeness, freshness, current authority or permission.
    """
    snapshot = require_snapshot_binding(profile, snapshot_payload, expected_snapshot)
    profile = snapshot["profile"]
    boundary = snapshot["removal_history_boundary"]
    if (history_payload is None) != (expected_history is None):
        raise ValueError("History payload and expected binding are required together")

    history_binding = None
    findings = None
    if history_payload is None:
        status = "history_missing"
    else:
        # Validate even when the snapshot boundary is unspecified. Do not hide a
        # changed or foreign declaration behind an unresolved report.
        history = require_history_binding(profile, history_payload, expected_history)
        history_binding = dict(expected_history)
        if boundary is None:
            status = "snapshot_boundary_unspecified"
        elif boundary != history["history_boundary"]:
            raise ValueError("Snapshot and history boundary conflict")
        else:
            status = "bound_declarations_compared"
            findings = inspect_restore(profile, history["requests"], snapshot["records"])["findings"]

    return {"version": 1, "profile": profile, "snapshot_id": snapshot["snapshot_id"],
            "snapshot_binding": dict(expected_snapshot),
            "history_binding": history_binding, "snapshot_history_boundary": boundary,
            "declared_coverage": snapshot["coverage"], "read_only": True,
            "comparison_status": status, "coverage": "caller_supplied_only",
            "authority": "caller_asserted_only", "freshness": "not_assessed",
            "reopening": "not_assessed", "findings": findings}

"""Compare three supplied bindings under one declared association, with no I/O."""
from __future__ import annotations

from .capture_binding import require_capture_note_binding
from .removal import _identity, _profile
from .request_binding import require_capture_request_binding
from .result_binding import require_capture_result_binding

SCHEME = "lumina-capture-binding-report-v1"


def verify_capture_bindings(profile: str, capture: dict, *, capture_id: str,
                            expected_notes: dict, expected_requests: dict,
                            expected_results: dict) -> dict:
    """Return a partial report only if all three retained envelopes match.

    Every comparison uses the same candidate, profile and capture ID. Delegated
    validators check exact envelope shapes, versions, digests and row contents.
    No payload, action permission or source authentication is returned. A common
    declared label does not prove that the rows came from one read transaction.
    """
    profile = _profile(profile)
    capture_id = _identity(capture_id, 200)
    notes = require_capture_note_binding(profile, capture, expected_notes,
                                         capture_id=capture_id)
    requests = require_capture_request_binding(profile, capture, expected_requests,
                                               capture_id=capture_id)
    results = require_capture_result_binding(profile, capture, expected_results,
                                             capture_id=capture_id)
    return dict(version=1, scheme=SCHEME, profile=profile, capture_id=capture_id,
                status="supplied_bindings_match",
                scope="supplied_note_request_result_rows", coverage="partial",
                common_snapshot="not_proven", authenticity="not_assessed",
                freshness="not_assessed", authority="not_assessed",
                counts=dict(notes=len(notes), requests=len(requests), results=len(results)),
                bindings=dict(notes_sha256=expected_notes["notes_sha256"],
                              requests_sha256=expected_requests["requests_sha256"],
                              results_sha256=expected_results["results_sha256"]))

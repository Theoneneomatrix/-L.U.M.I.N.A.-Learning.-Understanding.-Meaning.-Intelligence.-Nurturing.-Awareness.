"""Pure fixture projection of supplied journal exports, never archive integration."""
from __future__ import annotations

import hashlib
import json

from .history_binding import _parse as _parse_history
from .removal import _identity, _profile
from .restore import _digest
from .snapshot import _fields
from .snapshot_binding import _parse as _parse_snapshot


def _positive_id(value):
    if type(value) is not int or value <= 0:
        raise ValueError("Fixture note ID must be a positive integer")
    return value


def _list(value):
    if type(value) is not list or len(value) > 10000:
        raise ValueError("Fixture rows must be a bounded list")
    return value


def _encoded(value):
    try:
        return json.dumps(value, ensure_ascii=True, allow_nan=False).encode("ascii")
    except (ValueError, TypeError, RecursionError):
        raise ValueError("Invalid fixture declaration") from None


def project_fixture_declarations(profile: str, memory_export: dict,
                                 saved_intents: list[dict], mappings: list[dict], *,
                                 snapshot_id: str, history_boundary: str) -> dict:
    """Project explicitly supplied fixtures without opening files or databases.

    Each exported note requires one mapping containing note_id, record_key,
    target_key and content_sha256. The digest is SHA-256 of exact UTF-8 note
    text only, without trimming/normalization. It does NOT bind note metadata.
    IDs and target meanings remain caller assertions. No expected binding or
    permission is generated. Supplied exports need not be complete or current.
    """
    profile = _profile(profile)
    _fields(memory_export, ("format", "profile", "records", "results"))
    if memory_export["format"] != "lumina-memory-v1" or _profile(memory_export["profile"]) != profile:
        raise ValueError("Fixture export format or profile mismatch")
    _list(memory_export["results"])  # Not projected; never infer coverage from absence.
    notes = {}
    for row in _list(memory_export["records"]):
        if type(row) is not dict or not {"id", "profile", "text"} <= row.keys():
            raise ValueError("Fixture note requires id, profile and text")
        note_id = _positive_id(row["id"])
        if _profile(row["profile"]) != profile or note_id in notes:
            raise ValueError("Fixture note profile mismatch or duplicate ID")
        text = row["text"]
        if type(text) is not str or not text.strip() or len(text) > 20000:
            raise ValueError("Invalid fixture note text")
        try:
            notes[note_id] = hashlib.sha256(text.encode("utf-8", errors="strict")).hexdigest()
        except UnicodeError:
            raise ValueError("Invalid fixture note Unicode") from None

    records, mapped, keys, versions = [], set(), set(), set()
    for row in _list(mappings):
        _fields(row, ("note_id", "record_key", "target_key", "content_sha256"))
        note_id = _positive_id(row["note_id"])
        key = _identity(row["record_key"], 2000)
        target = _identity(row["target_key"], 2000)
        digest = _digest(row["content_sha256"])
        if note_id not in notes or notes[note_id] != digest:
            raise ValueError("Fixture mapping missing note or text digest conflict")
        if note_id in mapped or key in keys or (target, digest) in versions:
            raise ValueError("Ambiguous fixture mapping")
        mapped.add(note_id); keys.add(key); versions.add((target, digest))
        records.append(dict(profile=profile, record_key=key, target_key=target, content_sha256=digest))
    if mapped != set(notes):
        raise ValueError("Every supplied note requires an explicit mapping")
    records.sort(key=lambda row: row["record_key"])

    requests = []
    for row in _list(saved_intents):
        _fields(row, ("id", "version", "profile", "request_id", "target_key",
                      "content_sha256", "created_at", "state"))
        _positive_id(row["id"])
        _identity(row["created_at"], 200)  # Retained source field, not a freshness check.
        requests.append({key: row[key] for key in
                         ("version", "profile", "request_id", "target_key", "content_sha256", "state")})
    snapshot = {"version": 1, "profile": profile, "snapshot_id": snapshot_id,
                "removal_history_boundary": history_boundary,
                "coverage": {"scope": "profile_records", "declaration": "partial",
                             "exclusions": ["fixture:research-results-and-untracked-copies"]},
                "records": records}
    history = {"version": 1, "profile": profile, "history_boundary": history_boundary,
               "requests": requests}
    # Reuse strict v1 schemas, Unicode and byte bounds; return detached values.
    return {"snapshot": _parse_snapshot(profile, _encoded(snapshot)),
            "history": _parse_history(profile, _encoded(history))}

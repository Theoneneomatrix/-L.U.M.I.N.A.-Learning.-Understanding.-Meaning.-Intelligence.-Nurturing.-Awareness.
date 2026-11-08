"""Offline removal requests, not deletion receipts or an authorization mechanism."""
from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .memory import PROFILES


def _identity(value: str, limit: int) -> str:
    if (type(value) is not str or not value.strip() or len(value) > limit
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError(f"Identity must be nonempty text of at most {limit} characters without controls")
    return value.strip()


def _profile(value: str) -> str:
    value = _identity(value, 100).lower()
    if value not in PROFILES:
        raise ValueError("Unknown profile")
    return value


class RemovalIntentLedger:
    """Append-only through this interface; file owners can bypass these rules.

    The caller supplies the ledger path and stable target identity. No memory
    records are inspected, removed, hidden, or considered authorized by a request.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS removal_intents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version INTEGER NOT NULL CHECK(version=1),
                profile TEXT NOT NULL,
                request_id TEXT NOT NULL,
                target_key TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(profile, request_id)
            )""")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _record(row: sqlite3.Row) -> dict:
        return {**dict(row), "state": "requested"}

    def record(self, profile: str, request_id: str, *, target_key: str,
               content_sha256: str) -> dict:
        """Commit one v1 request; identical retry returns its original ID/time."""
        profile = _profile(profile)
        request_id = _identity(request_id, 200)
        target_key = _identity(target_key, 2000)
        if type(content_sha256) is not str or not re.fullmatch(r"[0-9a-f]{64}", content_sha256):
            raise ValueError("Content digest must be lowercase SHA-256 hex")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM removal_intents WHERE profile=? AND request_id=?",
                             (profile, request_id)).fetchone()
            if row is not None:
                if row["target_key"] != target_key or row["content_sha256"] != content_sha256:
                    raise ValueError("Removal request ID already has different intent")
            else:
                cursor = db.execute("""INSERT INTO removal_intents
                    (version,profile,request_id,target_key,content_sha256,created_at)
                    VALUES (1,?,?,?,?,?)""", (profile, request_id, target_key, content_sha256,
                                             datetime.now(UTC).isoformat()))
                row = db.execute("SELECT * FROM removal_intents WHERE id=?",
                                 (cursor.lastrowid,)).fetchone()
            result = self._record(row)
        return result

    def intents(self, profile: str) -> list[dict]:
        """Return only this profile's requests, in committed insertion order."""
        profile = _profile(profile)
        with self._connect() as db:
            return [self._record(row) for row in db.execute(
                "SELECT * FROM removal_intents WHERE profile=? ORDER BY id", (profile,))]

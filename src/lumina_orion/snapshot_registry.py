"""Offline first-binding persistence, not a restore permission or trust service."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .removal import _identity, _profile
from .snapshot_binding import bind_snapshot_manifest, require_snapshot_binding


class SnapshotBindingRegistry:
    """Preserve the first profile/snapshot binding through this interface.

    File owners can change or roll back the database. Registration is a caller
    assertion, not approval, authentication, freshness, or content verification.
    Only binding metadata is retained; no payload, timestamp, or reviewer text.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS snapshot_bindings (
                version INTEGER NOT NULL CHECK(version=1),
                scheme TEXT NOT NULL,
                profile TEXT NOT NULL,
                snapshot_id TEXT NOT NULL,
                manifest_sha256 TEXT NOT NULL,
                PRIMARY KEY(profile, snapshot_id)
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
    def _find(db, profile, snapshot_id):
        row = db.execute("""SELECT version,scheme,profile,snapshot_id,manifest_sha256
            FROM snapshot_bindings WHERE profile=? AND snapshot_id=?""",
                         (profile, snapshot_id)).fetchone()
        return dict(row) if row is not None else None

    def record(self, profile: str, payload: bytes) -> dict:
        """Commit a first binding, return an identical retry, reject changes.

        No overwrite, replacement, deletion, payload storage, or approval occurs.
        Parsing precedes the write reservation; lookup and insertion share it.
        Returns only after commit. Busy/I/O failures propagate to the caller.
        """
        candidate = bind_snapshot_manifest(profile, payload)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            saved = self._find(db, candidate["profile"], candidate["snapshot_id"])
            if saved is None:
                db.execute("""INSERT INTO snapshot_bindings
                    (version,scheme,profile,snapshot_id,manifest_sha256)
                    VALUES (?,?,?,?,?)""", tuple(candidate[k] for k in
                    ("version", "scheme", "profile", "snapshot_id", "manifest_sha256")))
                saved = candidate
            elif saved != candidate:
                raise ValueError("Snapshot identity already has a different binding")
        return saved

    def get(self, profile: str, snapshot_id: str) -> dict | None:
        """Read one scoped binding; absence is not permission to register/use it."""
        profile = _profile(profile)
        snapshot_id = _identity(snapshot_id, 200)
        with self._connect() as db:
            return self._find(db, profile, snapshot_id)

    def verify(self, profile: str, payload: bytes) -> dict:
        """Compare against a registered binding; never auto-register a missing one.

        Returns a detached manifest matching that read's binding, not authority
        to act on it. This does not reserve a transaction for a later operation.
        """
        candidate = bind_snapshot_manifest(profile, payload)
        saved = self.get(candidate["profile"], candidate["snapshot_id"])
        if saved is None:
            raise ValueError("No registered snapshot binding")
        return require_snapshot_binding(profile, payload, saved)

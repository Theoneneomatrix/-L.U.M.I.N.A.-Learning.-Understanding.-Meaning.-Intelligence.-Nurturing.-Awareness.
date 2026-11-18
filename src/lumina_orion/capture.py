"""Read a co-located local journal and request ledger without granting authority."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .memory import MemoryBank
from .removal import RemovalIntentLedger, _profile


def capture_journal(path: str | Path, profile: str) -> dict:
    """Return detached exports from one explicit read transaction.

    The existing file must contain all four journal/ledger tables. No tables,
    directories, mappings, bindings or capture timestamps are created. SQLite
    may use WAL coordination sidecars. This is not a schema/provenance audit,
    complete archive inventory, freshness check or permission to act.
    """
    profile = _profile(profile)
    uri = Path(path).resolve().as_uri() + "?mode=ro&cache=private"
    db = sqlite3.connect(uri, uri=True, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        db.execute("BEGIN")
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
        required = {"memories", "dependencies", "research_results", "removal_intents"}
        if not required <= tables:
            raise ValueError("Capture requires existing journal and removal ledger tables: "
                             + ", ".join(sorted(required - tables)))
        records = MemoryBank._records(db, profile, "ORDER BY m.id")
        results = [MemoryBank._result(row) for row in db.execute(
            "SELECT * FROM research_results WHERE profile=? ORDER BY run_id, step", (profile,))]
        intents = [RemovalIntentLedger._record(row) for row in db.execute(
            "SELECT * FROM removal_intents WHERE profile=? ORDER BY id", (profile,))]
        return dict(version=1, profile=profile,
                    memory_export=dict(format="lumina-memory-v1", profile=profile,
                                       records=records, results=results),
                    saved_intents=intents, consistency="single_read_transaction",
                    coverage="partial", freshness="not_assessed", authority="not_assessed")
    finally:
        db.close()

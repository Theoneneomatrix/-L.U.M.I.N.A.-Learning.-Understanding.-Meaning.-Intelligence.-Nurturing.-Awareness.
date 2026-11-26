"""Export one profile from an explicit existing local store, without applying actions."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys

from .capture import capture_journal

ERROR = "Capture export failed: unreadable or unsupported store, or invalid profile."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export stored records for one profile; coverage is partial and authority unassessed.",
        allow_abbrev=False)
    parser.add_argument("--db", required=True, help="Explicit path to an existing co-located journal/ledger")
    parser.add_argument("--profile", required=True, help="Family profile to export")
    args = parser.parse_args(argv)
    try:
        capture = capture_journal(args.db, args.profile)
        output = json.dumps(capture, sort_keys=True, ensure_ascii=True, allow_nan=False)
    except (OSError, sqlite3.Error, ValueError, RecursionError):
        print(ERROR, file=sys.stderr)
        return 2
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

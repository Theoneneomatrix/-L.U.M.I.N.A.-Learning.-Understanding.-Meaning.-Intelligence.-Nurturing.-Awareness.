"""Export one profile from an explicit existing local store, without applying actions."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

from .capture import capture_journal

OUTPUT_ERROR = "Capture export delivery failed: stdout may be incomplete."
MAX_OUTPUT_BYTES = 1024 * 1024
SIZE_ERROR = "Capture export failed: output exceeds 1 MiB; no capture was emitted."
ERROR = "Capture export failed: unreadable or unsupported store, or invalid profile."


def _quiet_failed_stdout() -> None:
    # Avoid another buffered-write error during interpreter shutdown. Only the
    # failed stdout descriptor is redirected; the journal is never reopened.
    try:
        descriptor = sys.stdout.fileno()
        with open(os.devnull, "wb") as sink:
            os.dup2(sink.fileno(), descriptor)
    except (OSError, ValueError, AttributeError):
        # Embedded streams may not expose a descriptor. Cleanup is best effort.
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export stored records for one profile; coverage is partial and authority unassessed.",
        allow_abbrev=False)
    parser.add_argument("--db", required=True, help="Explicit path to an existing co-located journal/ledger")
    parser.add_argument("--profile", required=True, help="Family profile to export")
    args = parser.parse_args(argv)
    try:
        capture = capture_journal(args.db, args.profile)
        output = json.dumps(capture, sort_keys=True, ensure_ascii=True, allow_nan=False).encode("utf-8") + b"\n"
    except (OSError, sqlite3.Error, ValueError, RecursionError):
        print(ERROR, file=sys.stderr)
        return 2
    if len(output) > MAX_OUTPUT_BYTES:
        print(SIZE_ERROR, file=sys.stderr)
        return 2
    try:
        if sys.stdout.buffer.write(output) != len(output):
            raise OSError("Incomplete stdout write")
        sys.stdout.buffer.flush()
    except (OSError, ValueError):
        _quiet_failed_stdout()
        print(OUTPUT_ERROR, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

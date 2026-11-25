"""Offline file inspection of supplied capture bindings; no registration or apply."""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from .capture_verification import verify_capture_bindings

MAX_INPUT_BYTES = 1024 * 1024
ERROR = "Capture verification failed: unreadable, invalid or mismatched input."


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _constant(value):
    raise ValueError("Non-finite JSON number")


def _float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Non-finite JSON number")
    return number


def _read(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("Input exceeds byte limit")
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=_object,
                       parse_constant=_constant, parse_float=_float)
    if type(value) is not dict:
        raise ValueError("Expected JSON object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare supplied capture bindings; the report is partial, not action permission.",
        allow_abbrev=False)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--capture-id", required=True)
    for name in ("capture", "notes", "requests", "results"):
        parser.add_argument("--" + name, required=True, help="Existing local UTF-8 JSON file")
    args = parser.parse_args(argv)
    try:
        report = verify_capture_bindings(
            args.profile, _read(args.capture), capture_id=args.capture_id,
            expected_notes=_read(args.notes), expected_requests=_read(args.requests),
            expected_results=_read(args.results))
        output = json.dumps(report, sort_keys=True, ensure_ascii=True, allow_nan=False)
    except (OSError, ValueError, RecursionError):
        print(ERROR, file=sys.stderr)
        return 2
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

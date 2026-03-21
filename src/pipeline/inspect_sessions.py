"""Inspect sessions.jsonl — filter by message_count threshold."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/sessions.jsonl", type=Path)
    parser.add_argument("--min", default=10, type=int, dest="min_count",
                        help="Show sessions with message_count > MIN")
    args = parser.parse_args()

    with args.input.open(encoding="utf-8") as f:
        sessions = [json.loads(l) for l in f if l.strip()]

    matched = [s for s in sessions if s["message_count"] > args.min_count]

    print(f"Sessions with message_count > {args.min_count}: {len(matched)} / {len(sessions)}\n")
    print(f"{'session_id':<12} {'start':<20} {'end':<20} {'msgs':>5}  {'parent':<10}")
    print("-" * 72)
    for s in matched:
        parent = s["parent_session_id"] or "-"
        print(f"{s['session_id']:<12} {s['start']:<20} {s['end']:<20} {s['message_count']:>5}  {parent:<10}")


if __name__ == "__main__":
    main()

"""
Stage 2: segment.py
Groups flat messages (JSONL) into sessions by time gap and burst limit.

Rule A (Silence):  gap > 4 hours  → new session
Rule B (Burst):    > 100 messages  → force-split
Rule C (Inertia):  if session[0].reference_id == prev_session[-1].id
                   → annotate with parent_session_id (no merge, just pointer)
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

# ── Config ───────────────────────────────────────────────────────────────────

SILENCE_HOURS: float = 4.0
BURST_LIMIT: int = 100
TIMESTAMP_FMT: str = "%Y-%m-%d %H:%M:%S"

# ── Data model ───────────────────────────────────────────────────────────────

@dataclass
class Session:
    session_id: str
    start: str
    end: str
    message_count: int
    parent_session_id: str | None
    messages: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


# ── Helpers ──────────────────────────────────────────────────────────────────

def parse_ts(ts: str) -> datetime:
    return datetime.strptime(ts, TIMESTAMP_FMT)


def gap_hours(a: str, b: str) -> float:
    return (parse_ts(b) - parse_ts(a)).total_seconds() / 3600


def session_id(index: int) -> str:
    return f"S{index:04d}"


# ── Core segmentation ────────────────────────────────────────────────────────

def segment(messages: list[dict[str, Any]]) -> list[Session]:
    if not messages:
        return []

    sessions: list[Session] = []
    current: list[dict[str, Any]] = [messages[0]]

    def flush(buf: list[dict[str, Any]]) -> Session:
        idx = len(sessions) + 1
        return Session(
            session_id=session_id(idx),
            start=buf[0]["timestamp"],
            end=buf[-1]["timestamp"],
            message_count=len(buf),
            parent_session_id=None,
            messages=list(buf),
        )

    for msg in messages[1:]:
        prev = current[-1]

        # Rule A: silence threshold
        silence_break = gap_hours(prev["timestamp"], msg["timestamp"]) > SILENCE_HOURS
        # Rule B: burst limit
        burst_break = len(current) >= BURST_LIMIT

        if silence_break or burst_break:
            sessions.append(flush(current))
            current = [msg]
        else:
            current.append(msg)

    # Flush last buffer
    if current:
        sessions.append(flush(current))

    # Rule C: inertia — annotate parent pointer
    msg_to_session: dict[str, str] = {}
    for s in sessions:
        for m in s.messages:
            msg_to_session[m["id"]] = s.session_id

    for s in sessions:
        first_msg = s.messages[0]
        ref_id = first_msg.get("reference_id")
        if ref_id and ref_id in msg_to_session:
            parent_sid = msg_to_session[ref_id]
            if parent_sid != s.session_id:
                s.parent_session_id = parent_sid

    return sessions


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Segment messages → sessions JSONL")
    parser.add_argument("--input", default="data/messages.jsonl", type=Path)
    parser.add_argument("--output", default="data/sessions.jsonl", type=Path)
    args = parser.parse_args()

    messages: list[dict[str, Any]] = []
    with args.input.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                messages.append(json.loads(line))

    sessions = segment(messages)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as fout:
        for s in sessions:
            fout.write(json.dumps(s.to_dict(), ensure_ascii=False) + "\n")

    inertia_count = sum(1 for s in sessions if s.parent_session_id)
    print(
        f"[segment] {len(messages)} messages → {len(sessions)} sessions"
        f" ({inertia_count} with inertia link) → {args.output}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()

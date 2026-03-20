"""
profile_members.py
Scan sessions.jsonl → find all participants → generate per-member personality
profiles via Gemini CLI → data/members/<author_id>.md

Usage:
  python profile_members.py [--sessions data/sessions.jsonl]
                            [--output-dir data/members]
                            [--min-messages 50]
                            [--sample 200]
                            [--model <name>] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import textwrap
from collections import defaultdict
from pathlib import Path
from typing import Any

# ── Config ────────────────────────────────────────────────────────────────────

MIN_MESSAGES_DEFAULT = 50
SAMPLE_DEFAULT = 200  # max messages to feed into profile prompt

PROFILE_SYSTEM = textwrap.dedent("""\
    You are a character analyst. Given a sample of Discord chat messages from one person,
    write a concise personality profile that a language model can use to understand
    and predict this person's behaviour.

    Output format (plain text):
    ---
    [Display Name / Handle]

    **Linguistic Style**
    <2-3 sentences: vocabulary, tone, emoji use, language mix, sentence length>

    **Relational Role**
    <2-3 sentences: how they function in group chat — initiator, reactor, roaster, advisor, etc.>

    **Emotional Baseline**
    <1-2 sentences: dominant mood, emotional range>

    **Interests & Topics**
    <comma-separated list of recurring topics>

    **How to interact with them**
    <2-3 sentences: what kind of responses they appreciate, what they react to, conversational dynamics>
    ---

    Rules:
    - Only describe traits evidenced in the messages. Do not invent.
    - Write in English.
    - Be specific: quote or paraphrase 1-2 examples if helpful.
""").strip()


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_sessions(path: Path) -> list[dict[str, Any]]:
    sessions: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                sessions.append(json.loads(line))
    return sessions


def collect_members(
    sessions: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Return {author_id: {display_names: set, messages: list}} for all participants."""
    members: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"display_names": set(), "messages": []}
    )
    for session in sessions:
        for msg in session.get("messages", []):
            aid = msg.get("author_id")
            if not aid:
                continue
            members[aid]["display_names"].add(msg.get("display_name", "?"))
            members[aid]["messages"].append(msg)
    return dict(members)


def sample_messages(messages: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    """Take evenly-spaced sample across all messages to capture style over time."""
    if len(messages) <= n:
        return messages
    step = len(messages) / n
    return [messages[int(i * step)] for i in range(n)]


def build_prompt(handle: str, messages: list[dict[str, Any]]) -> str:
    lines = [f"{PROFILE_SYSTEM}\n\nAnalyse this person: {handle}\n\nMessage sample:\n"]
    for m in messages:
        lines.append(f"[{m['timestamp'][:10]}] {m['display_name']}: {m['content']}")
    lines.append("\nNow write the personality profile.")
    return "\n".join(lines)


def call_gemini(prompt: str, model: str | None) -> tuple[str, bool]:
    cmd = ["gemini"]
    if model:
        cmd += ["--model", model]
    result = subprocess.run(cmd, input=prompt, capture_output=True, text=True)
    if result.returncode != 0:
        return result.stderr.strip(), False
    return result.stdout.strip(), True


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate per-member personality profiles from sessions"
    )
    parser.add_argument("--sessions", default="data/sessions.jsonl", type=Path)
    parser.add_argument("--output-dir", default="data/members", type=Path)
    parser.add_argument("--min-messages", default=MIN_MESSAGES_DEFAULT, type=int,
                        help="Minimum message count to profile a member (default 50)")
    parser.add_argument("--sample", default=SAMPLE_DEFAULT, type=int,
                        help="Max messages to include in prompt (default 200)")
    parser.add_argument("--model", default=None)
    parser.add_argument("--dry-run", action="store_true",
                        help="Print summary and first prompt only, skip Gemini calls")
    args = parser.parse_args()

    if not args.sessions.exists():
        print(f"[error] sessions not found: {args.sessions}", file=sys.stderr)
        sys.exit(1)

    args.output_dir.mkdir(parents=True, exist_ok=True)

    sessions = load_sessions(args.sessions)
    members = collect_members(sessions)

    # Filter and sort by message count descending
    qualifying = [
        (aid, data)
        for aid, data in members.items()
        if len(data["messages"]) >= args.min_messages
    ]
    qualifying.sort(key=lambda x: len(x[1]["messages"]), reverse=True)

    print(
        f"[profile_members] {len(members)} unique authors → "
        f"{len(qualifying)} qualify (≥{args.min_messages} messages)"
        + (" | DRY RUN" if args.dry_run else ""),
        file=sys.stderr,
    )

    for i, (aid, data) in enumerate(qualifying, 1):
        out_path = args.output_dir / f"{aid}.md"
        handle = ", ".join(sorted(data["display_names"]))
        msg_count = len(data["messages"])

        if out_path.exists() and not args.dry_run:
            print(f"  [{i}/{len(qualifying)}] skip {handle} ({msg_count} msgs) — already exists",
                  file=sys.stderr)
            continue

        print(f"  [{i}/{len(qualifying)}] profiling {handle} ({msg_count} msgs)...",
              file=sys.stderr)

        sample = sample_messages(data["messages"], args.sample)
        prompt = build_prompt(handle, sample)

        if args.dry_run:
            print(f"\n--- PROMPT PREVIEW: {handle} ---", file=sys.stderr)
            print(prompt[:800] + ("..." if len(prompt) > 800 else ""), file=sys.stderr)
            if i == 1:
                continue  # only show first in dry-run
            break

        raw, success = call_gemini(prompt, args.model)
        if not success:
            print(f"  [error] gemini failed for {handle}: {raw[:100]}", file=sys.stderr)
            continue

        out_path.write_text(raw, encoding="utf-8")
        print(f"  → {out_path}", file=sys.stderr)

    print("[profile_members] done", file=sys.stderr)


if __name__ == "__main__":
    main()

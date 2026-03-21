"""
Stage 3: profile.py
Sends each session to Claude → Personality Fragment (JSONL).

Each fragment covers four pillars:
  - linguistic_style   : slang, emoji density, sentence length, tone
  - relational_role    : listener / roaster / advice-giver / hype-man …
  - emotional_baseline : cynical / optimistic / dry / anxious …
  - knowledge_graph    : recurring topics, shared jokes, niche interests

Usage:
  python profile.py [--input data/sessions.jsonl] [--output data/fragments.jsonl]
                    [--target <author_id>] [--min-messages 3]
                    [--concurrency 4] [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import anthropic

# ── Config ───────────────────────────────────────────────────────────────────

MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 1024
MIN_MESSAGES_DEFAULT = 3

SYSTEM_PROMPT = """\
You are a personality analyst. Given a Discord chat session transcript, extract a \
structured personality fragment for the specified target user.

Return ONLY a valid JSON object with exactly these keys:
{
  "linguistic_style": "...",
  "relational_role": "...",
  "emotional_baseline": "...",
  "knowledge_graph": ["topic1", "topic2", ...]
}

Guidelines:
- linguistic_style: describe vocabulary, sentence length, use of slang/emoji/profanity, \
  typing habits (e.g. "short bursts, heavy emoji, mix of English and Traditional Chinese, \
  occasional swearing").
- relational_role: how this person functions in group chat dynamics (e.g. "primary topic \
  initiator, often roasts others, de-escalates tension with humor").
- emotional_baseline: underlying emotional tone and temperament (e.g. "dry and sarcastic, \
  occasional bursts of genuine enthusiasm, low-key cynical").
- knowledge_graph: list of 3–8 topics, interests, or recurring references shown in this session.

If the target user has fewer than 2 messages in this session, return null.\
"""


def format_transcript(session: dict[str, Any], target_display: str | None) -> str:
    """Format session messages as a readable transcript."""
    lines: list[str] = [
        f"Session {session['session_id']} | {session['start']} → {session['end']}",
        f"Messages: {session['message_count']}",
    ]
    if target_display:
        lines.append(f"Target user: {target_display}")
    lines.append("---")
    for msg in session["messages"]:
        lines.append(f"[{msg['timestamp']}] {msg['display_name']}: {msg['content']}")
    return "\n".join(lines)


def build_user_prompt(session: dict[str, Any], target_display: str | None) -> str:
    transcript = format_transcript(session, target_display)
    if target_display:
        return (
            f"Analyze the personality of **{target_display}** based on the session below.\n\n"
            f"{transcript}"
        )
    return f"Analyze the personality of ALL participants in the session below.\n\n{transcript}"


# ── API call ─────────────────────────────────────────────────────────────────

async def extract_fragment(
    client: anthropic.AsyncAnthropic,
    session: dict[str, Any],
    target_id: str | None,
    target_display: str | None,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any] | None:
    """Call Claude and return a fragment dict, or None on failure."""
    async with semaphore:
        user_prompt = build_user_prompt(session, target_display)
        try:
            response = await client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            raw = response.content[0].text.strip()
            # Strip markdown code fences if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            fragment_data = json.loads(raw)
            if fragment_data is None:
                return None
        except (json.JSONDecodeError, IndexError, anthropic.APIError) as e:
            print(f"  [warn] {session['session_id']}: {e}", file=sys.stderr)
            return None

    return {
        "session_id": session["session_id"],
        "start": session["start"],
        "end": session["end"],
        "target_id": target_id,
        "target_display": target_display,
        **fragment_data,
    }


# ── Main ─────────────────────────────────────────────────────────────────────

def load_sessions(path: Path) -> list[dict[str, Any]]:
    sessions = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                sessions.append(json.loads(line))
    return sessions


def load_done_ids(path: Path) -> set[str]:
    """Return set of session_ids already in output file (for resume)."""
    done: set[str] = set()
    if not path.exists():
        return done
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    done.add(json.loads(line)["session_id"])
                except (json.JSONDecodeError, KeyError):
                    pass
    return done


def filter_sessions(
    sessions: list[dict[str, Any]],
    target_id: str | None,
    min_messages: int,
) -> list[dict[str, Any]]:
    result = []
    for s in sessions:
        if s["message_count"] < min_messages:
            continue
        if target_id:
            # Only include sessions where target participated
            participants = {m["author_id"] for m in s["messages"]}
            if target_id not in participants:
                continue
        result.append(s)
    return result


async def run(
    input_path: Path,
    output_path: Path,
    target_id: str | None,
    min_messages: int,
    concurrency: int,
    dry_run: bool,
) -> None:
    sessions = load_sessions(input_path)
    sessions = filter_sessions(sessions, target_id, min_messages)
    done_ids = load_done_ids(output_path)

    # Build target display name from first matching message
    target_display: str | None = None
    if target_id:
        for s in sessions:
            for m in s["messages"]:
                if m["author_id"] == target_id:
                    target_display = m["display_name"]
                    break
            if target_display:
                break

    pending = [s for s in sessions if s["session_id"] not in done_ids]

    print(
        f"[profile] {len(sessions)} sessions after filter "
        f"({len(done_ids)} already done, {len(pending)} pending)"
        f"{' | DRY RUN' if dry_run else ''}",
        file=sys.stderr,
    )

    if dry_run:
        sample = pending[:3] if pending else sessions[:3]
        for s in sample:
            print(f"\n--- {s['session_id']} ---", file=sys.stderr)
            print(build_user_prompt(s, target_display), file=sys.stderr)
        return

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("[error] ANTHROPIC_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    client = anthropic.AsyncAnthropic(api_key=api_key)
    semaphore = asyncio.Semaphore(concurrency)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    completed = 0
    errors = 0

    async def process(session: dict[str, Any], fout: Any) -> None:
        nonlocal completed, errors
        fragment = await extract_fragment(client, session, target_id, target_display, semaphore)
        if fragment:
            fout.write(json.dumps(fragment, ensure_ascii=False) + "\n")
            fout.flush()
            completed += 1
        else:
            errors += 1
        total = completed + errors
        if total % 50 == 0 or total == len(pending):
            print(
                f"  [{total}/{len(pending)}] done={completed} err={errors}",
                file=sys.stderr,
            )

    with output_path.open("a", encoding="utf-8") as fout:
        tasks = [process(s, fout) for s in pending]
        await asyncio.gather(*tasks)

    print(
        f"[profile] finished: {completed} fragments, {errors} errors → {output_path}",
        file=sys.stderr,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Profile sessions via LLM → fragments JSONL")
    parser.add_argument("--input", default="data/sessions.jsonl", type=Path)
    parser.add_argument("--output", default="data/fragments.jsonl", type=Path)
    parser.add_argument(
        "--target",
        default=None,
        help="Filter to sessions containing this author_id",
    )
    parser.add_argument("--min-messages", default=MIN_MESSAGES_DEFAULT, type=int)
    parser.add_argument("--concurrency", default=4, type=int, help="Parallel API calls")
    parser.add_argument("--dry-run", action="store_true", help="Print prompts, skip API calls")
    args = parser.parse_args()

    asyncio.run(
        run(
            input_path=args.input,
            output_path=args.output,
            target_id=args.target,
            min_messages=args.min_messages,
            concurrency=args.concurrency,
            dry_run=args.dry_run,
        )
    )


if __name__ == "__main__":
    main()

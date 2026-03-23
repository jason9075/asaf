"""
Stage 3: profile.py
Sends each session to Claude → Knowledge Graph Fragment (SQLite).

Each fragment is structured for a SQLite-backed memory/RAG system:
  - session_metadata   : primary topic, confidence score, urgency level
  - participants       : user roles and contribution weights
  - discussion_outline : timestamped sections with summaries and resolution status
  - indexing_metadata  : technologies, entities, error signatures, concepts, action items

Usage:
  python profile.py [--input data/sessions.jsonl] [--db db/asaf.db]
                    [--target <author_id>] [--min-messages 10]
                    [--concurrency 4] [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Config ───────────────────────────────────────────────────────────────────

MIN_MESSAGES_DEFAULT = 10

SYSTEM_PROMPT = """\
Role: You are a Contextual Data Engineer specializing in dialogue decomposition and \
knowledge graph extraction.

Task: Analyze the provided Discord chat transcript. Transform raw conversation into a \
structured JSON object optimized for storage in a SQLite-backed memory system.

Output Requirements:
Return ONLY a valid JSON object. If the session contains fewer than 2 meaningful messages, \
return null.

JSON Schema:
{
  "session_metadata": {
    "primary_topic": "Short, descriptive title of the main discussion",
    "confidence_score": 0.0,
    "urgency_level": "low/medium/high"
  },
  "participants": [
    {
      "user_id": "Discord numeric author_id (e.g., 123456789012345678)",
      "role": "e.g., Questioner, Expert, Facilitator, Casual Observer",
      "contribution_weight": 0.0
    }
  ],
  "discussion_outline": [
    {
      "timestamp_range": "e.g., 10:00 - 10:15",
      "section_title": "...",
      "summary": "Concise technical summary",
      "resolved": true
    }
  ],
  "indexing_metadata": {
    "technologies": ["Specific tools, languages, or frameworks"],
    "entities": ["Projects, organizations, or specific hardware models"],
    "error_signatures": ["Specific error codes or log patterns mentioned"],
    "concepts": ["High-level architectural patterns or methodologies"],
    "action_items": ["Tasks or next steps explicitly agreed upon"]
  }
}

Extraction Guidelines:
1. Indexing Priority: In indexing_metadata, prioritize high-entropy technical terms \
(e.g., NixOS, LSP, FTS5) over generic words (e.g., code, problem).
2. Entity Normalization: Standardize naming (e.g., convert "nvim" to "Neovim").
3. Noise Reduction: Ignore bot commands, social pleasantries (e.g., "Good morning"), \
and stickers unless they convey a decision.
4. SQLite Optimization: Ensure indexing_metadata arrays are clean and unique, as these \
will be used for Full-Text Search (FTS) and relational mapping.
5. Context Switching: If the participants jump between unrelated topics, create distinct \
entries in the discussion_outline.\
"""


def format_transcript(session: dict[str, Any], target_display: str | None = None) -> str:
    """Format session messages as a readable transcript."""
    lines: list[str] = [
        f"Session {session['session_id']} | {session['start']} → {session['end']}",
        f"Messages: {session['message_count']}",
        "---",
    ]
    for msg in session["messages"]:
        lines.append(f"[{msg['timestamp']}] {msg['display_name']} ({msg['author_id']}): {msg['content']}")
    return "\n".join(lines)


def build_prompt(session: dict[str, Any]) -> str:
    transcript = format_transcript(session)
    return (
        f"{SYSTEM_PROMPT}\n\n"
        f"Analyze the following Discord session and return the structured JSON.\n\n"
        f"{transcript}"
    )


# ── Gemini call ───────────────────────────────────────────────────────────────

def call_gemini(prompt: str, model: str | None) -> tuple[str, bool]:
    """Pipe prompt to gemini CLI via stdin. Returns (stdout, success)."""
    cmd = ["gemini"]
    if model:
        cmd += ["--model", model]
    result = subprocess.run(cmd, input=prompt, capture_output=True, text=True)
    if result.returncode != 0:
        return result.stderr.strip(), False
    return result.stdout.strip(), True


def parse_fragment(raw: str) -> dict[str, Any] | None:
    """Extract and parse JSON from LLM response. Returns None on failure."""
    text = raw.strip()

    # Strip markdown fences if present
    if "```" in text:
        parts = text.split("```")
        # parts[1] is the fenced block content
        if len(parts) >= 3:
            text = parts[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

    # Try direct parse first
    try:
        data = json.loads(text)
        return data if data is not None else None
    except json.JSONDecodeError:
        pass

    # Fallback: extract outermost { ... } block
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            return data if data is not None else None
        except json.JSONDecodeError:
            pass

    return None


async def extract_fragment(
    session: dict[str, Any],
    model: str | None,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any] | None:
    """Call gemini and return a fragment dict, or None on failure."""
    async with semaphore:
        prompt = build_prompt(session)
        raw, success = await asyncio.to_thread(call_gemini, prompt, model)
        if not success:
            print(f"  [warn] {session['session_id']}: gemini error: {raw[:80]}", file=sys.stderr)
            return None
        fragment_data = parse_fragment(raw)
        if fragment_data is None:
            preview = raw[:120].replace("\n", " ")
            print(f"  [warn] {session['session_id']}: JSON parse failed | {preview}", file=sys.stderr)
            return None

    return {
        "session_id": session["session_id"],
        "start": session["start"],
        "end": session["end"],
        "model": model,
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


_DDL = """\
CREATE TABLE IF NOT EXISTS profile_fragments (
    session_id         TEXT PRIMARY KEY,
    start              TEXT NOT NULL,
    end                TEXT NOT NULL,
    session_metadata   TEXT NOT NULL,
    participants       TEXT NOT NULL,
    discussion_outline TEXT NOT NULL,
    indexing_metadata  TEXT NOT NULL,
    created_at         TEXT NOT NULL,
    model              TEXT
);
"""


def init_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(_DDL)
    # Migration: add model column to existing tables that predate this column
    try:
        conn.execute("ALTER TABLE profile_fragments ADD COLUMN model TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists
    conn.commit()
    return conn


def load_done_ids(conn: sqlite3.Connection) -> set[str]:
    """Return set of session_ids already stored in profile_fragments."""
    rows = conn.execute("SELECT session_id FROM profile_fragments").fetchall()
    return {r[0] for r in rows}


def save_fragment(conn: sqlite3.Connection, fragment: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO profile_fragments
            (session_id, start, end, session_metadata, participants,
             discussion_outline, indexing_metadata, created_at, model)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fragment["session_id"],
            fragment["start"],
            fragment["end"],
            json.dumps(fragment.get("session_metadata", {}), ensure_ascii=False),
            json.dumps(fragment.get("participants", []), ensure_ascii=False),
            json.dumps(fragment.get("discussion_outline", []), ensure_ascii=False),
            json.dumps(fragment.get("indexing_metadata", {}), ensure_ascii=False),
            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            fragment.get("model"),
        ),
    )
    conn.commit()


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
    db_path: Path,
    target_id: str | None,
    min_messages: int,
    concurrency: int,
    model: str | None,
    dry_run: bool,
) -> None:
    sessions = load_sessions(input_path)
    sessions = filter_sessions(sessions, target_id, min_messages)

    conn = init_db(db_path)
    done_ids = load_done_ids(conn)
    pending = [s for s in sessions if s["session_id"] not in done_ids]

    print(
        f"[profile] {len(sessions)} sessions after filter "
        f"({len(done_ids)} already done, {len(pending)} pending)"
        f"{' | DRY RUN' if dry_run else ''}",
        file=sys.stderr,
    )

    if dry_run:
        conn.close()
        sample = pending[:3] if pending else sessions[:3]
        for s in sample:
            print(f"\n--- {s['session_id']} ---", file=sys.stderr)
            print(build_prompt(s), file=sys.stderr)
        return

    semaphore = asyncio.Semaphore(concurrency)
    completed = 0
    errors = 0

    async def process(session: dict[str, Any]) -> None:
        nonlocal completed, errors
        fragment = await extract_fragment(session, model, semaphore)
        if fragment:
            save_fragment(conn, fragment)
            completed += 1
        else:
            errors += 1
        total = completed + errors
        if total % 50 == 0 or total == len(pending):
            print(
                f"  [{total}/{len(pending)}] done={completed} err={errors}",
                file=sys.stderr,
            )

    tasks = [process(s) for s in pending]
    await asyncio.gather(*tasks)
    conn.close()

    print(
        f"[profile] finished: {completed} fragments, {errors} errors → {db_path}",
        file=sys.stderr,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Profile sessions via LLM → SQLite fragments")
    parser.add_argument("--input", default="data/sessions.jsonl", type=Path)
    parser.add_argument("--db", default="db/asaf.db", type=Path)
    parser.add_argument(
        "--target",
        default=None,
        help="Filter to sessions containing this author_id",
    )
    parser.add_argument("--min-messages", default=MIN_MESSAGES_DEFAULT, type=int,
                        help=f"Min messages per session (default: {MIN_MESSAGES_DEFAULT})")
    parser.add_argument("--concurrency", default=4, type=int, help="Parallel gemini calls")
    parser.add_argument("--model", default=None, help="Gemini model name (optional)")
    parser.add_argument("--dry-run", action="store_true", help="Print prompts, skip gemini call")
    args = parser.parse_args()

    asyncio.run(
        run(
            input_path=args.input,
            db_path=args.db,
            target_id=args.target,
            min_messages=args.min_messages,
            concurrency=args.concurrency,
            model=args.model,
            dry_run=args.dry_run,
        )
    )


if __name__ == "__main__":
    main()

"""
analyze.py
Filter sessions (message_count >= 10) → batch every 10 → call gemini CLI
→ store raw data + analysis in db/asaf.db (SQLite).

Usage:
  python analyze.py [--sessions data/sessions.jsonl] [--db db/asaf.db]
                    [--batch-size 10] [--min-messages 10]
                    [--dry-run] [--model <name>]
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Schema ────────────────────────────────────────────────────────────────────

DDL = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id        TEXT PRIMARY KEY,
    start             TEXT NOT NULL,
    end               TEXT NOT NULL,
    message_count     INTEGER NOT NULL,
    parent_session_id TEXT,
    messages          TEXT NOT NULL   -- JSON array
);

CREATE TABLE IF NOT EXISTS batches (
    batch_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    session_ids  TEXT NOT NULL,       -- JSON array of session_id
    prompt       TEXT NOT NULL,
    raw_response TEXT,
    status       TEXT NOT NULL DEFAULT 'pending',  -- pending / done / error
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fragments (
    session_id       TEXT PRIMARY KEY,
    batch_id         INTEGER NOT NULL,
    linguistic_style TEXT,
    relational_role  TEXT,
    emotional_baseline TEXT,
    knowledge_graph  TEXT,            -- JSON array
    FOREIGN KEY (batch_id) REFERENCES batches(batch_id)
);
"""

# ── Prompt builder ────────────────────────────────────────────────────────────

SYSTEM_INSTRUCTIONS = textwrap.dedent("""\
    You are a personality analyst for Discord group chats.
    Analyze each session and return ONLY a JSON array — no markdown, no explanation.
    Each element must have exactly these keys:
      session_id, linguistic_style, relational_role, emotional_baseline, knowledge_graph
    knowledge_graph is a list of 3–8 strings (topics / recurring references).
    If a session has too little content to analyse, set all text fields to null
    and knowledge_graph to [].
""").strip()


def format_session_block(session: dict[str, Any]) -> str:
    lines = [f"=== {session['session_id']} | {session['start']} → {session['end']} ==="]
    for msg in session["messages"]:
        lines.append(f"[{msg['timestamp']}] {msg['display_name']}: {msg['content']}")
    return "\n".join(lines)


def build_prompt(batch: list[dict[str, Any]]) -> str:
    blocks = "\n\n".join(format_session_block(s) for s in batch)
    session_ids = ", ".join(s["session_id"] for s in batch)
    return (
        f"{SYSTEM_INSTRUCTIONS}\n\n"
        f"Sessions to analyse: {session_ids}\n\n"
        f"{blocks}\n\n"
        f"Return a JSON array with {len(batch)} objects, one per session above."
    )


# ── Gemini call ───────────────────────────────────────────────────────────────

def call_gemini(prompt: str, model: str | None) -> tuple[str, bool]:
    """Pipe prompt to gemini CLI via stdin. Returns (stdout, success)."""
    cmd = ["gemini"]
    if model:
        cmd += ["--model", model]
    result = subprocess.run(
        cmd,
        input=prompt,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return result.stderr.strip(), False
    return result.stdout.strip(), True


def parse_response(raw: str) -> list[dict[str, Any]] | None:
    """Extract JSON array from gemini response (strip markdown fences if any)."""
    text = raw.strip()
    if text.startswith("```"):
        # strip ```json ... ``` fences
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass
    return None


# ── DB helpers ────────────────────────────────────────────────────────────────

def init_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(DDL)
    conn.commit()
    return conn


def upsert_sessions(conn: sqlite3.Connection, sessions: list[dict[str, Any]]) -> None:
    conn.executemany(
        """
        INSERT OR IGNORE INTO sessions
            (session_id, start, end, message_count, parent_session_id, messages)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (
                s["session_id"],
                s["start"],
                s["end"],
                s["message_count"],
                s.get("parent_session_id"),
                json.dumps(s["messages"], ensure_ascii=False),
            )
            for s in sessions
        ],
    )
    conn.commit()


def done_session_ids(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT session_id FROM fragments").fetchall()
    return {r["session_id"] for r in rows}


def save_batch(
    conn: sqlite3.Connection,
    session_ids: list[str],
    prompt: str,
    raw_response: str,
    status: str,
    fragments: list[dict[str, Any]],
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "INSERT INTO batches (session_ids, prompt, raw_response, status, created_at) VALUES (?,?,?,?,?)",
        (json.dumps(session_ids), prompt, raw_response, status, now),
    )
    batch_id = cur.lastrowid

    if fragments:
        conn.executemany(
            """
            INSERT OR REPLACE INTO fragments
                (session_id, batch_id, linguistic_style, relational_role,
                 emotional_baseline, knowledge_graph)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    f.get("session_id"),
                    batch_id,
                    f.get("linguistic_style"),
                    f.get("relational_role"),
                    f.get("emotional_baseline"),
                    json.dumps(f.get("knowledge_graph", []), ensure_ascii=False),
                )
                for f in fragments
            ],
        )
    conn.commit()


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Batch-analyse sessions via gemini → SQLite")
    parser.add_argument("--sessions", default="data/sessions.jsonl", type=Path)
    parser.add_argument("--db", default="db/asaf.db", type=Path)
    parser.add_argument("--min-messages", default=10, type=int)
    parser.add_argument("--batch-size", default=10, type=int)
    parser.add_argument("--model", default=None, help="Gemini model name (optional)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    # Load & filter
    with args.sessions.open(encoding="utf-8") as f:
        all_sessions = [json.loads(l) for l in f if l.strip()]
    filtered = [s for s in all_sessions if s["message_count"] >= args.min_messages]
    filtered.sort(key=lambda s: s["session_id"])

    # Init DB
    conn = init_db(args.db)
    upsert_sessions(conn, filtered)

    # Skip already done
    done = done_session_ids(conn)
    pending = [s for s in filtered if s["session_id"] not in done]

    total_batches = math.ceil(len(pending) / args.batch_size)
    print(
        f"[analyze] {len(filtered)} sessions (>={args.min_messages} msgs) | "
        f"{len(done)} done | {len(pending)} pending | {total_batches} batches"
        + (" | DRY RUN" if args.dry_run else ""),
        file=sys.stderr,
    )

    if not pending:
        print("[analyze] nothing to do.", file=sys.stderr)
        return

    ok = err = 0
    for i in range(0, len(pending), args.batch_size):
        batch = pending[i : i + args.batch_size]
        batch_num = i // args.batch_size + 1
        batch_sids = [s["session_id"] for s in batch]
        prompt = build_prompt(batch)

        print(
            f"  batch {batch_num}/{total_batches} "
            f"[{batch_sids[0]}..{batch_sids[-1]}]",
            end=" ",
            file=sys.stderr,
        )

        if args.dry_run:
            print("(dry)", file=sys.stderr)
            print(f"\n{'─'*60}\n{prompt[:800]}...\n", file=sys.stderr)
            continue

        raw, success = call_gemini(prompt, args.model)

        if not success:
            print(f"ERROR: {raw[:120]}", file=sys.stderr)
            save_batch(conn, batch_sids, prompt, raw, "error", [])
            err += 1
            continue

        fragments = parse_response(raw)
        if fragments is None:
            print(f"PARSE_ERROR | raw: {raw[:80]}", file=sys.stderr)
            save_batch(conn, batch_sids, prompt, raw, "error", [])
            err += 1
            continue

        save_batch(conn, batch_sids, prompt, raw, "done", fragments)
        ok += 1
        print(f"ok ({len(fragments)} fragments)", file=sys.stderr)
        for frag in fragments:
            print(json.dumps(frag, ensure_ascii=False))

    if not args.dry_run:
        print(f"[analyze] done: {ok} batches ok, {err} errors → {args.db}", file=sys.stderr)

    conn.close()


if __name__ == "__main__":
    main()

"""
Stage 4: synthesize.py
Aggregate all Personality Fragments → Master Personality Profile (LLM system prompt).

Reads fragments from SQLite (db/asaf.db) or JSONL (data/fragments.jsonl),
calls gemini CLI via `gemini -p`, and writes the synthesized profile to stdout
(and optionally to a file).

Usage:
  python synthesize.py [--db db/asaf.db] [--fragments data/fragments.jsonl]
                       [--output data/profile.md] [--model <name>] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

# ── Prompt ────────────────────────────────────────────────────────────────────

SYNTHESIS_SYSTEM = textwrap.dedent("""\
    You are a character writer synthesizing Discord chat analysis into a concise,
    vivid personality profile suitable as an LLM system prompt.

    You will receive multiple personality fragments, each analysed from a separate
    chat session. Synthesize them into ONE cohesive Master Personality Profile.

    Output format (plain text, no markdown headers needed):
    ---
    [Name / Handle]

    **Linguistic Style**
    <2–4 sentences>

    **Relational Role**
    <2–4 sentences>

    **Emotional Baseline**
    <2–4 sentences>

    **Knowledge & Interests**
    <comma-separated list of recurring topics, then 1–2 sentences of context>

    **System Prompt (copy-paste ready)**
    <A first-person or third-person instruction block that an LLM can use to
     roleplay or respond in the style of this person. 3–6 sentences.>
    ---

    Rules:
    - Resolve contradictions across fragments by favouring the majority signal.
    - Preserve idiosyncratic quirks that appear in ≥2 fragments.
    - Do NOT invent traits not evidenced in the fragments.
    - Write in English unless the fragments indicate a non-English dominant style.
""").strip()


def build_prompt(fragments: list[dict[str, Any]], handle: str | None) -> str:
    handle_line = f"Target: {handle}\n\n" if handle else ""
    fragment_blocks: list[str] = []
    for i, f in enumerate(fragments, 1):
        kg = f.get("knowledge_graph")
        if isinstance(kg, str):
            # stored as JSON string in SQLite
            try:
                kg = json.loads(kg)
            except json.JSONDecodeError:
                pass
        kg_str = ", ".join(kg) if isinstance(kg, list) else str(kg or "")
        block = (
            f"--- Fragment {i} | session {f.get('session_id', '?')} ---\n"
            f"linguistic_style: {f.get('linguistic_style', '')}\n"
            f"relational_role: {f.get('relational_role', '')}\n"
            f"emotional_baseline: {f.get('emotional_baseline', '')}\n"
            f"knowledge_graph: {kg_str}"
        )
        fragment_blocks.append(block)

    body = "\n\n".join(fragment_blocks)
    return (
        f"{SYNTHESIS_SYSTEM}\n\n"
        f"{handle_line}"
        f"Fragments ({len(fragments)} total):\n\n"
        f"{body}\n\n"
        "Now write the Master Personality Profile."
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


# ── Fragment loaders ──────────────────────────────────────────────────────────

def load_from_db(db_path: Path) -> list[dict[str, Any]]:
    if not db_path.exists():
        print(f"[error] DB not found: {db_path}", file=sys.stderr)
        sys.exit(1)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT session_id, linguistic_style, relational_role, "
        "emotional_baseline, knowledge_graph FROM fragments"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def load_from_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        print(f"[error] JSONL not found: {path}", file=sys.stderr)
        sys.exit(1)
    fragments: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                fragments.append(json.loads(line))
    return fragments


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Synthesize personality fragments → master profile via gemini"
    )
    parser.add_argument("--db", default="db/asaf.db", type=Path,
                        help="SQLite DB written by analyze.py (default source)")
    parser.add_argument("--fragments", default=None, type=Path,
                        help="JSONL fallback (from profile.py)")
    parser.add_argument("--output", default=None, type=Path,
                        help="Write profile to this file (also printed to stdout)")
    parser.add_argument("--handle", default=None,
                        help="Person's name/handle to include in prompt")
    parser.add_argument("--model", default=None,
                        help="Gemini model name (optional)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print prompt only, skip gemini call")
    args = parser.parse_args()

    # Load fragments — prefer DB, fall back to JSONL
    if args.fragments:
        fragments = load_from_jsonl(args.fragments)
        source = str(args.fragments)
    else:
        fragments = load_from_db(args.db)
        source = str(args.db)

    # Filter out empty fragments
    fragments = [
        f for f in fragments
        if any(f.get(k) for k in ("linguistic_style", "relational_role", "emotional_baseline"))
    ]

    print(
        f"[synthesize] {len(fragments)} usable fragments from {source}"
        + (" | DRY RUN" if args.dry_run else ""),
        file=sys.stderr,
    )

    if not fragments:
        print("[error] no fragments to synthesize", file=sys.stderr)
        sys.exit(1)

    prompt = build_prompt(fragments, args.handle)

    if args.dry_run:
        print(f"\n{'─' * 60}", file=sys.stderr)
        print(prompt[:1200] + ("..." if len(prompt) > 1200 else ""), file=sys.stderr)
        return

    print("[synthesize] calling gemini...", file=sys.stderr)
    raw, success = call_gemini(prompt, args.model)

    if not success:
        print(f"[error] gemini failed: {raw}", file=sys.stderr)
        sys.exit(1)

    print(raw)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(raw, encoding="utf-8")
        print(f"[synthesize] profile written → {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()

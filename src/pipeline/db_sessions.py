#!/usr/bin/env python3
"""Import data/sessions.jsonl → db/asaf.db (sessions table)."""
import argparse
import json
import sqlite3
from pathlib import Path


def ingest_sessions_to_db(sessions_jsonl: Path, db_path: Path) -> int:
    if not sessions_jsonl.exists():
        return 0
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id        TEXT PRIMARY KEY,
                start             TEXT NOT NULL,
                end               TEXT NOT NULL,
                message_count     INTEGER NOT NULL,
                parent_session_id TEXT,
                messages          TEXT NOT NULL
            )
        """)
        count = 0
        with sessions_jsonl.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                s = json.loads(line)
                conn.execute(
                    "INSERT OR REPLACE INTO sessions "
                    "(session_id, start, end, message_count, parent_session_id, messages) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        s["session_id"],
                        s.get("start", ""),
                        s.get("end", ""),
                        s.get("message_count", len(s.get("messages", []))),
                        s.get("parent_session_id"),
                        json.dumps(s.get("messages", []), ensure_ascii=False),
                    ),
                )
                count += 1
        conn.commit()
        return count
    finally:
        conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/sessions.jsonl", type=Path)
    parser.add_argument("--db", default="db/asaf.db", type=Path)
    args = parser.parse_args()

    n = ingest_sessions_to_db(args.input, args.db)
    print(f"Imported {n} sessions → {args.db}")

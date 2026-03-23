"""SQLite memory: search, retrieve, and log bot exchanges."""
import json
import re
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

from .config import MEMORY_SESSIONS, MEMORY_MSGS_PER_SESSION, RECALL_LIMIT

_STOP = {
    "的", "了", "嗎", "是", "在", "我", "你", "他", "她", "它",
    "我們", "你們", "他們", "這", "那", "就", "都", "也", "還",
    "and", "the", "is", "a", "an", "to", "of", "in", "for", "that",
}


def search_memory(context: str, db_path: Path) -> str:
    """Search SQLite for historical sessions relevant to the current context."""
    if not db_path.exists():
        return ""

    words = [
        w for w in re.findall(r"\w+", context)
        if len(w) > 2 and w.lower() not in _STOP
    ]
    if not words:
        return ""

    conn = sqlite3.connect(db_path)
    seen: set[str] = set()
    snippets: list[str] = []
    try:
        for word in words[:8]:
            if len(seen) >= MEMORY_SESSIONS:
                break
            try:
                rows = conn.execute(
                    "SELECT session_id, start, messages FROM sessions "
                    "WHERE messages LIKE ? LIMIT 3",
                    (f"%{word}%",),
                ).fetchall()
            except sqlite3.OperationalError:
                break  # sessions table not yet created
            for session_id, start, messages_json in rows:
                if session_id in seen or len(seen) >= MEMORY_SESSIONS:
                    continue
                seen.add(session_id)
                msgs: list[dict] = json.loads(messages_json)
                hits = [
                    m for m in msgs
                    if word.lower() in m.get("content", "").lower()
                ][:MEMORY_MSGS_PER_SESSION]
                if hits:
                    lines = "\n".join(
                        f"  [{m['timestamp'][:10]}] {m['display_name']}: {m['content']}"
                        for m in hits
                    )
                    snippets.append(f"[Session {start[:10]}]\n{lines}")
    finally:
        conn.close()
    return "\n\n".join(snippets)


def search_sessions_for_recall(
    query: str, db_path: Path, limit: int = RECALL_LIMIT
) -> list[dict]:
    """Search sessions for query keywords.

    Returns list of {session_id, start, preview_msgs, jump_msg_id}.
    """
    if not db_path.exists():
        return []
    words = [
        w for w in re.findall(r"\w+", query)
        if len(w) > 2 and w.lower() not in _STOP
    ]
    if not words:
        return []

    conn = sqlite3.connect(db_path)
    seen: set[str] = set()
    results: list[dict] = []
    try:
        for word in words[:8]:
            if len(seen) >= limit:
                break
            rows = conn.execute(
                "SELECT session_id, start, messages FROM sessions "
                "WHERE messages LIKE ? ORDER BY start DESC LIMIT 5",
                (f"%{word}%",),
            ).fetchall()
            for session_id, start, messages_json in rows:
                if session_id in seen or len(seen) >= limit:
                    continue
                seen.add(session_id)
                msgs: list[dict] = json.loads(messages_json)
                hits = [
                    m for m in msgs
                    if word.lower() in m.get("content", "").lower()
                ][:4]
                if not hits:
                    continue
                jump_msg_id = msgs[0]["id"] if msgs else ""
                results.append({
                    "session_id": session_id,
                    "start": start,
                    "preview_msgs": hits,
                    "jump_msg_id": jump_msg_id,
                })
    finally:
        conn.close()
    return results


def expand_query_to_english(query: str, model: str | None = None) -> list[str]:
    """Translate/transliterate Chinese proper nouns in query to English keywords.

    Returns a list of English search terms to augment the original keyword list.
    """
    prompt = (
        "Extract the key proper nouns and topics from this query and return their "
        "English transliterations or translations. "
        "Return ONLY comma-separated English terms, no explanation.\n"
        f"Query: {query}"
    )
    cmd = ["gemini"]
    if model:
        cmd += ["--model", model]
    try:
        result = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd="/tmp", timeout=10)
        if result.returncode != 0 or not result.stdout.strip():
            return []
        raw = result.stdout.strip()
        return [w for w in re.findall(r"\w+", raw) if len(w) > 1 and w.lower() not in _STOP]
    except Exception:
        return []


def recall_random_fragment(db_path: Path) -> str:
    """Pick a random profile_fragment and format it as context for the model."""
    if not db_path.exists():
        return ""
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT session_id, start, session_metadata, discussion_outline, indexing_metadata "
            "FROM profile_fragments ORDER BY RANDOM() LIMIT 1"
        ).fetchone()
        if not row:
            return ""
        sid, start, meta_raw, outline_raw, idx_raw = row
        meta = json.loads(meta_raw)
        outline: list[dict] = json.loads(outline_raw)
        idx = json.loads(idx_raw)
        topic = meta.get("primary_topic", "")
        summaries = [o["summary"] for o in outline if o.get("summary")]
        entities = idx.get("entities", [])
        concepts = idx.get("concepts", [])
        lines = [f"[Fragment {sid} | {start[:10]}] {topic}"]
        for s in summaries[:3]:
            lines.append(f"  概要: {s}")
        if entities:
            lines.append(f"  提到的人/事/物: {', '.join(entities[:6])}")
        if concepts:
            lines.append(f"  討論概念: {', '.join(concepts[:5])}")
        return "\n".join(lines)
    except sqlite3.OperationalError:
        return ""
    finally:
        conn.close()


def rag_recall(
    query: str,
    db_path: Path,
    limit: int = 5,
    random_fallback: bool = False,
    model: str | None = None,
) -> tuple[str, bool]:
    """RAG retrieval: search profile_fragments then sessions for relevant context.

    Returns (context_str, is_random).
    is_random=True means context came from random sampling, not keyword search.
    Priority: profile_fragments (structured) → sessions (raw content fallback).
    If model is provided, Chinese proper nouns are also translated to English keywords.
    If random_fallback=True and no results found, returns a random fragment instead.
    """
    if not db_path.exists():
        return "", False

    words = [w for w in re.findall(r"\w+", query) if len(w) > 1 and w.lower() not in _STOP]

    # Expand with English translation to handle Chinese→English entity name mismatch
    if model and words:
        english_words = expand_query_to_english(query, model)
        if english_words:
            seen_words: set[str] = set(w.lower() for w in words)
            words = words + [w for w in english_words if w.lower() not in seen_words]
            print(f"[memory] rag_recall expanded keywords: {words}", flush=True)

    if not words:
        return (recall_random_fragment(db_path), True) if random_fallback else ("", False)

    conn = sqlite3.connect(db_path)
    parts: list[str] = []
    seen: set[str] = set()

    try:
        # ── 1. profile_fragments (structured RAG) ─────────────────────────────
        for word in words[:8]:
            if len(seen) >= limit:
                break
            like = f"%{word}%"
            try:
                rows = conn.execute(
                    """
                    SELECT session_id, start, session_metadata,
                           discussion_outline, indexing_metadata
                    FROM profile_fragments
                    WHERE json_extract(session_metadata, '$.primary_topic') LIKE ?
                       OR json_extract(indexing_metadata, '$.entities')     LIKE ?
                       OR json_extract(indexing_metadata, '$.concepts')     LIKE ?
                       OR json_extract(indexing_metadata, '$.technologies') LIKE ?
                    ORDER BY start DESC LIMIT 5
                    """,
                    (like, like, like, like),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []

            for sid, start, meta_raw, outline_raw, idx_raw in rows:
                if sid in seen or len(seen) >= limit:
                    continue
                seen.add(sid)
                meta = json.loads(meta_raw)
                outline: list[dict] = json.loads(outline_raw)
                idx = json.loads(idx_raw)

                topic = meta.get("primary_topic", "")
                summaries = [o["summary"] for o in outline if o.get("summary")]
                entities = idx.get("entities", [])
                concepts = idx.get("concepts", [])
                action_items = idx.get("action_items", [])

                lines = [f"[Fragment {sid} | {start[:10]}] {topic}"]
                for s in summaries[:3]:
                    lines.append(f"  概要: {s}")
                if entities:
                    lines.append(f"  提到的人/事/物: {', '.join(entities[:6])}")
                if concepts:
                    lines.append(f"  討論概念: {', '.join(concepts[:5])}")
                if action_items:
                    lines.append(f"  行動項目: {', '.join(action_items[:3])}")
                parts.append("\n".join(lines))

        # ── 2. sessions raw content (fallback) ────────────────────────────────
        if len(seen) < limit:
            for word in words[:6]:
                if len(seen) >= limit:
                    break
                try:
                    rows = conn.execute(
                        "SELECT session_id, start, messages FROM sessions "
                        "WHERE messages LIKE ? ORDER BY start DESC LIMIT 3",
                        (f"%{word}%",),
                    ).fetchall()
                except sqlite3.OperationalError:
                    break  # sessions table doesn't exist

                for sid, start, messages_json in rows:
                    if sid in seen or len(seen) >= limit:
                        continue
                    seen.add(sid)
                    msgs: list[dict] = json.loads(messages_json)
                    hits = [
                        m for m in msgs
                        if word.lower() in m.get("content", "").lower()
                    ][:4]
                    if not hits:
                        continue
                    lines = [f"[Session {sid} | {start[:10]}]"]
                    for m in hits:
                        content = m["content"][:120]
                        lines.append(f"  [{m['timestamp'][:10]}] {m['display_name']}: {content}")
                    parts.append("\n".join(lines))

    finally:
        conn.close()

    if not parts and random_fallback:
        return recall_random_fragment(db_path), True

    return "\n\n".join(parts), False


def format_recall_reply(results: list[dict], guild_id: str, channel_id: str) -> str:
    if not results:
        return "找不到相關的過去對話 🤔"

    lines = [f"找到 {len(results)} 段相關對話："]
    for r in results:
        date = r["start"][:10]
        lines.append(f"\n📅 {date}")
        for m in r["preview_msgs"]:
            content = m["content"][:60] + ("…" if len(m["content"]) > 60 else "")
            lines.append(f"  {m['display_name']}: {content}")
        if r["jump_msg_id"] and guild_id and channel_id:
            url = f"https://discord.com/channels/{guild_id}/{channel_id}/{r['jump_msg_id']}"
            lines.append(f"  → {url}")
    return "\n".join(lines)


def build_member_map(db_path: Path) -> dict[str, list[str]]:
    """Scan SQLite sessions and build {author_id: [known display names]} map."""
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(db_path)
    member_map: dict[str, set[str]] = {}
    try:
        rows = conn.execute("SELECT messages FROM sessions").fetchall()
        for (messages_json,) in rows:
            msgs: list[dict] = json.loads(messages_json)
            for m in msgs:
                aid = m.get("author_id")
                name = m.get("display_name")
                if aid and name:
                    member_map.setdefault(aid, set()).add(name)
    finally:
        conn.close()
    return {aid: sorted(names) for aid, names in member_map.items()}


def get_member_history(author_id: str, db_path: Path) -> str:
    """Retrieve recent messages by a specific author_id from SQLite."""
    if not db_path.exists():
        return ""
    conn = sqlite3.connect(db_path)
    snippets: list[str] = []
    try:
        try:
            rows = conn.execute(
                "SELECT start, messages FROM sessions "
                "WHERE messages LIKE ? ORDER BY start DESC LIMIT ?",
                (f'%"author_id": "{author_id}"%', MEMORY_SESSIONS),
            ).fetchall()
        except sqlite3.OperationalError:
            return ""  # sessions table not yet created
        for start, messages_json in rows:
            msgs: list[dict] = json.loads(messages_json)
            hits = [m for m in msgs if m.get("author_id") == author_id][:MEMORY_MSGS_PER_SESSION]
            if hits:
                lines = "\n".join(
                    f"  [{m['timestamp'][:10]}] {m['display_name']}: {m['content']}"
                    for m in hits
                )
                snippets.append(f"[Session {start[:10]}]\n{lines}")
    finally:
        conn.close()
    return "\n\n".join(snippets)


def set_silence(db_path: Path, duration_minutes: int, requested_by: str) -> None:
    """Upsert a single silence record — always exactly one row."""
    from datetime import timedelta
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS silence_log (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                start        TEXT NOT NULL,
                end          TEXT NOT NULL,
                requested_by TEXT NOT NULL
            )
        """)
        now = datetime.now()
        end = now + timedelta(minutes=duration_minutes)
        conn.execute("DELETE FROM silence_log")
        conn.execute(
            "INSERT INTO silence_log (start, end, requested_by) VALUES (?, ?, ?)",
            (now.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S"), requested_by),
        )
        conn.commit()
    finally:
        conn.close()


def end_silence(db_path: Path) -> None:
    """Expire all active silence records immediately."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS silence_log (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                start        TEXT NOT NULL,
                end          TEXT NOT NULL,
                requested_by TEXT NOT NULL
            )
        """)
        conn.execute("DELETE FROM silence_log")
        conn.commit()
    finally:
        conn.close()


def is_silenced(db_path: Path) -> bool:
    """Return True if there is an active silence record right now."""
    if not db_path.exists():
        return False
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS silence_log (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                start        TEXT NOT NULL,
                end          TEXT NOT NULL,
                requested_by TEXT NOT NULL
            )
        """)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        row = conn.execute(
            "SELECT 1 FROM silence_log WHERE start <= ? AND end > ? LIMIT 1",
            (now, now),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def log_bot_exchange(
    db_path: Path,
    channel_id: str,
    sender_id: str,
    sender_label: str,
    user_msg: str,
    reply: str,
    model: str | None,
    input_prompt: str = "",
) -> None:
    """Append one bot exchange to bot_logs, creating the table if needed."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bot_logs (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp    TEXT NOT NULL,
                channel_id   TEXT NOT NULL,
                sender_id    TEXT NOT NULL,
                sender_label TEXT NOT NULL,
                user_msg     TEXT NOT NULL,
                input_prompt TEXT NOT NULL,
                reply        TEXT NOT NULL,
                model        TEXT
            )
        """)
        conn.execute(
            "INSERT INTO bot_logs "
            "(timestamp, channel_id, sender_id, sender_label, user_msg, input_prompt, reply, model) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                channel_id,
                sender_id,
                sender_label,
                user_msg,
                input_prompt,
                reply,
                model,
            ),
        )
        conn.commit()
    finally:
        conn.close()

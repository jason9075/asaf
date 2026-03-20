"""
Discord bot: @mention → gemini (persona from profile.md) → reply
30% chance to auto-chime in; LLM decides if there's a good opportunity.
Retrieves relevant historical conversations from SQLite as memory.
"""
import asyncio
import json
import os
import random
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import discord
from dotenv import load_dotenv

load_dotenv()

PROFILE_PATH = Path(os.getenv("PROFILE_PATH", "data/profile.md"))
MEMBERS_DIR = Path(os.getenv("MEMBERS_DIR", "data/members"))
GEMINI_MODEL = os.getenv("GEMINI_MODEL", None)
HISTORY_LIMIT = int(os.getenv("HISTORY_LIMIT", "30"))
DB_PATH = Path(os.getenv("DB_PATH", "db/asaf.db"))
MEMORY_SESSIONS = int(os.getenv("MEMORY_SESSIONS", "3"))   # max sessions to retrieve
MEMORY_MSGS_PER_SESSION = int(os.getenv("MEMORY_MSGS_PER_SESSION", "5"))  # msgs per session

# Common stop words to skip when building search keywords
_STOP = {
    "的", "了", "嗎", "是", "在", "我", "你", "他", "她", "它",
    "我們", "你們", "他們", "這", "那", "就", "都", "也", "還",
    "and", "the", "is", "a", "an", "to", "of", "in", "for", "that",
}


def load_profile(path: Path) -> str:
    """Extract the 'System Prompt' section from profile.md, falling back to full text."""
    if not path.exists():
        print(f"[warn] profile not found: {path}", file=sys.stderr)
        return ""
    text = path.read_text(encoding="utf-8")
    # Extract content after the "System Prompt" heading
    match = re.search(
        r"\*\*System Prompt.*?\*\*\s*\n+(.+)",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if match:
        return match.group(1).strip()
    return text.strip()


def search_memory(context: str, db_path: Path) -> str:
    """Search SQLite for historical sessions relevant to the current context."""
    if not db_path.exists():
        return ""

    # Extract keywords: words longer than 2 chars, not in stop list
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
        for word in words[:8]:  # cap keyword iterations
            if len(seen) >= MEMORY_SESSIONS:
                break
            rows = conn.execute(
                "SELECT session_id, start, messages FROM sessions "
                "WHERE messages LIKE ? LIMIT 3",
                (f"%{word}%",),
            ).fetchall()
            for session_id, start, messages_json in rows:
                if session_id in seen or len(seen) >= MEMORY_SESSIONS:
                    continue
                seen.add(session_id)
                msgs: list[dict] = json.loads(messages_json)
                # Keep only messages that actually contain the keyword
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


SILENT = "SILENT"


def call_gemini(
    system: str,
    history: str,
    user_msg: str,
    model: str | None,
    must_reply: bool = False,
    memory: str = "",
    sender_label: str = "",
    sender_profile: str = "",
) -> str:
    no_ack = (
        "IMPORTANT: Never start with acknowledgments like '收到', '了解', 'ready', "
        "'understood', 'setup received', or any similar phrase. "
        "Jump straight into the response as if you are mid-conversation."
    )
    if must_reply:
        instruction = (
            f"{no_ack} "
            "You ARE this person — not an AI, not an assistant. "
            "Respond naturally in Traditional Chinese as this person would when texting friends."
        )
    else:
        instruction = (
            f"{no_ack} "
            "You ARE this person — not an AI, not an assistant. "
            "You happened to glance at this conversation. "
            "If something genuinely catches your attention — a joke to riff on, "
            "a topic you care about, something worth reacting to — "
            "respond naturally in Traditional Chinese as this person would. "
            f"If nothing is worth saying, output exactly one word: {SILENT}"
        )

    sender_profile_block = (
        f"--- Who you're talking to ---\n{sender_profile}\n"
        f"--- End of member profile ---\n\n"
        if sender_profile else ""
    )
    memory_block = (
        f"--- Relevant past conversations (long-term memory) ---\n{memory}\n"
        f"--- End of memory ---\n\n"
        if memory else ""
    )

    prompt = (
        f"{system}\n\n"
        f"{sender_profile_block}"
        f"{memory_block}"
        f"--- Recent conversation (last {HISTORY_LIMIT} messages) ---\n"
        f"{history}\n"
        f"--- End of history ---\n\n"
        f"The person talking to you now: {sender_label}\n" if sender_label else ""
        f"Latest message: {user_msg}\n\n"
        f"{instruction}"
    )
    cmd = ["gemini"]
    if model:
        cmd += ["--model", model]
    result = subprocess.run(cmd, input=prompt, capture_output=True, text=True)
    if result.returncode != 0:
        return f"[error] {result.stderr.strip()[:200]}"
    return result.stdout.strip()


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
        rows = conn.execute(
            "SELECT start, messages FROM sessions "
            "WHERE messages LIKE ? ORDER BY start DESC LIMIT ?",
            (f'%"author_id": "{author_id}"%', MEMORY_SESSIONS),
        ).fetchall()
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


def load_member_profiles(members_dir: Path) -> dict[str, str]:
    """Load all data/members/<author_id>.md → {author_id: profile_text}."""
    profiles: dict[str, str] = {}
    if not members_dir.exists():
        return profiles
    for path in members_dir.glob("*.md"):
        author_id = path.stem
        profiles[author_id] = path.read_text(encoding="utf-8").strip()
    return profiles


intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
profile_text: str = ""
member_map: dict[str, list[str]] = {}
member_profiles: dict[str, str] = {}


@client.event
async def on_ready() -> None:
    global profile_text, member_map, member_profiles
    profile_text = load_profile(PROFILE_PATH)
    member_map = build_member_map(DB_PATH)
    member_profiles = load_member_profiles(MEMBERS_DIR)
    print(
        f"[bot] logged in as {client.user} | "
        f"profile: {len(profile_text)} chars | "
        f"members: {len(member_map)} known | "
        f"member profiles: {len(member_profiles)} loaded"
    )


@client.event
async def on_message(message: discord.Message) -> None:
    print(f"[debug] on_message: {message.author} → {message.content!r}", flush=True)
    if message.author == client.user:
        return

    mentioned = client.user in message.mentions
    # 30% random chance to consider chiming in (skipped if already mentioned)
    if not mentioned and random.random() > 0.30:
        return

    user_msg = message.content.replace(f"<@{client.user.id}>", "").strip()
    if not user_msg:
        return

    # Fetch last HISTORY_LIMIT messages (excluding the current one)
    history_lines: list[str] = []
    async for msg in message.channel.history(limit=HISTORY_LIMIT + 1, before=message):
        author = msg.author.display_name
        content = msg.content.replace(f"<@{client.user.id}>", "@bot").strip()
        history_lines.append(f"{author}: {content}")
    history_lines.reverse()  # chronological order
    history = "\n".join(history_lines) if history_lines else "(no prior messages)"

    loop = asyncio.get_event_loop()

    # Resolve sender identity from member_map (stable across name changes)
    sender_id = str(message.author.id)
    known_names = member_map.get(sender_id, [])
    sender_label = (
        f"{message.author.display_name} (also known as: {', '.join(known_names)})"
        if known_names else message.author.display_name
    )

    # Fetch sender's historical messages from SQLite by author_id
    sender_history = await loop.run_in_executor(
        None, lambda: get_member_history(sender_id, DB_PATH)
    )

    # Search SQLite for topic-relevant memory
    search_context = f"{user_msg} {' '.join(history_lines[-5:])}"
    topic_memory = await loop.run_in_executor(
        None, lambda: search_memory(search_context, DB_PATH)
    )

    # Compose memory block: sender identity + topic snippets
    memory_parts: list[str] = []
    if sender_history:
        memory_parts.append(f"[Past messages from {sender_label}]\n{sender_history}")
    if topic_memory:
        memory_parts.append(f"[Topic-related past conversations]\n{topic_memory}")
    memory = "\n\n".join(memory_parts)

    sender_profile = member_profiles.get(sender_id, "")

    if memory or sender_profile:
        print(
            f"[bot] context: sender_profile={'yes' if sender_profile else 'no'}, "
            f"sender_history={'yes' if sender_history else 'no'}, "
            f"topic={topic_memory.count('[Session')} session(s)",
            flush=True,
        )

    async with message.channel.typing():
        reply = await loop.run_in_executor(
            None,
            lambda: call_gemini(
                profile_text, history, user_msg, GEMINI_MODEL,
                must_reply=mentioned, memory=memory,
                sender_label=sender_label, sender_profile=sender_profile,
            ),
        )

    if reply == SILENT:
        print(f"[bot] chose to stay silent", flush=True)
        return

    await message.channel.send(reply)


def main() -> None:
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        print("[error] DISCORD_BOT_TOKEN not set", file=sys.stderr)
        sys.exit(1)
    client.run(token)


if __name__ == "__main__":
    main()

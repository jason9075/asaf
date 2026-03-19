"""
Stage 1: ingest.py
Cleans raw Discord JSON export → flat JSONL of sanitized messages.

Identity resolution:  nickname > global_name > username
Content sanitization: mentions, attachments, stickers, URLs
Timestamp:            ISO 8601 → "YYYY-MM-DD HH:mm:ss" (UTC+8 preserved)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Any

# ── Constants ────────────────────────────────────────────────────────────────

URL_RE = re.compile(r"https?://([^\s/]+)(\S*)")
MENTION_RE = re.compile(r"<@!?(\d+)>")
CHANNEL_RE = re.compile(r"<#\d+>")
ROLE_RE = re.compile(r"<@&\d+>")
EMOJI_RE = re.compile(r"<a?:[a-zA-Z0-9_]+:\d+>")

TZ_TAIPEI = timezone(timedelta(hours=8))

# ── Helpers ──────────────────────────────────────────────────────────────────

def resolve_display_name(author: dict[str, Any]) -> str:
    """nickname > global_name > username"""
    return (
        author.get("nickname")
        or author.get("globalName")
        or author["name"]
    )


def build_mention_table(messages: list[dict[str, Any]]) -> dict[str, str]:
    """Build author_id → display_name lookup from all messages."""
    table: dict[str, str] = {}
    for msg in messages:
        a = msg["author"]
        table[a["id"]] = resolve_display_name(a)
        for mention in msg.get("mentions", []):
            table[mention["id"]] = resolve_display_name(mention)
    return table


def normalize_timestamp(ts: str) -> str:
    """Convert any ISO 8601 string to 'YYYY-MM-DD HH:mm:ss' in UTC+8."""
    # Python 3.10 fromisoformat doesn't handle trailing Z or fractional tz
    ts = ts.rstrip("Z")
    # Handle fractional seconds longer than 6 digits
    ts = re.sub(r"(\.\d{6})\d+", r"\1", ts)
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ_TAIPEI)
    dt = dt.astimezone(TZ_TAIPEI)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def sanitize_content(
    content: str,
    mention_table: dict[str, str],
) -> str:
    """Replace Discord markup with human-readable tokens."""
    # Mentions: <@!id> / <@id> → @DisplayName
    def replace_mention(m: re.Match) -> str:
        uid = m.group(1)
        name = mention_table.get(uid, uid)
        return f"@{name}"

    content = MENTION_RE.sub(replace_mention, content)
    content = CHANNEL_RE.sub("[Channel]", content)
    content = ROLE_RE.sub("[Role]", content)
    # Custom emoji → keep only the name
    content = EMOJI_RE.sub(lambda m: f":{m.group(0).split(':')[1]}:", content)
    # URLs → [URL: domain.com]
    content = URL_RE.sub(lambda m: f"[URL: {m.group(1)}]", content)
    return content.strip()


def attachment_tag(attachment: dict[str, Any]) -> str:
    ct: str = attachment.get("contentType", "")
    if ct.startswith("image/"):
        return "[Sent an Image]"
    if ct.startswith("video/"):
        return "[Sent a Video]"
    if ct.startswith("audio/"):
        return "[Sent an Audio]"
    return "[Sent a File]"


def flatten_message(
    msg: dict[str, Any],
    mention_table: dict[str, str],
) -> dict[str, Any] | None:
    """Return a flat, sanitized message dict, or None to skip."""
    # Skip system messages (pin notifications, joins, etc.)
    if msg.get("type") not in ("Default", "Reply"):
        return None
    # Skip bots
    if msg["author"].get("isBot"):
        return None

    content = sanitize_content(msg.get("content", ""), mention_table)

    # Append media tags to content
    media_tags = [attachment_tag(a) for a in msg.get("attachments", [])]
    for sticker in msg.get("stickers", []):
        media_tags.append(f"[Sticker: {sticker.get('name', '?')}]")
    if media_tags:
        content = (content + " " + " ".join(media_tags)).strip()

    # Skip entirely empty messages
    if not content:
        return None

    reference_id: str | None = None
    ref = msg.get("reference")
    if ref and ref.get("type") == "Default":
        reference_id = ref.get("messageId")

    return {
        "id": msg["id"],
        "timestamp": normalize_timestamp(msg["timestamp"]),
        "author_id": msg["author"]["id"],
        "display_name": resolve_display_name(msg["author"]),
        "content": content,
        "reference_id": reference_id,
    }


# ── Main ─────────────────────────────────────────────────────────────────────

def ingest(input_path: Path, output_path: Path) -> int:
    raw = json.loads(input_path.read_text(encoding="utf-8"))
    messages: list[dict[str, Any]] = raw["messages"]

    mention_table = build_mention_table(messages)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output_path.open("w", encoding="utf-8") as fout:
        for msg in messages:
            flat = flatten_message(msg, mention_table)
            if flat is None:
                continue
            fout.write(json.dumps(flat, ensure_ascii=False) + "\n")
            count += 1

    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest Discord JSON → JSONL")
    parser.add_argument("--input", default="raw_json/gossip.json", type=Path)
    parser.add_argument("--output", default="data/messages.jsonl", type=Path)
    args = parser.parse_args()

    count = ingest(args.input, args.output)
    print(f"[ingest] {count} messages → {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()

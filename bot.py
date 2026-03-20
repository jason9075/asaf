"""
Discord bot: @mention → gemini (persona from profile.md) → reply
30% chance to auto-chime in; LLM decides if there's a good opportunity.
"""
import asyncio
import os
import random
import subprocess
import sys
from pathlib import Path

import discord
from dotenv import load_dotenv

load_dotenv()

PROFILE_PATH = Path(os.getenv("PROFILE_PATH", "data/profile.md"))
GEMINI_MODEL = os.getenv("GEMINI_MODEL", None)
HISTORY_LIMIT = int(os.getenv("HISTORY_LIMIT", "30"))


def load_profile(path: Path) -> str:
    if not path.exists():
        print(f"[warn] profile not found: {path}", file=sys.stderr)
        return ""
    return path.read_text(encoding="utf-8").strip()


SILENT = "SILENT"


def call_gemini(
    system: str,
    history: str,
    user_msg: str,
    model: str | None,
    must_reply: bool = False,
) -> str:
    if must_reply:
        instruction = "Reply in the persona above."
    else:
        instruction = (
            "You randomly decided to consider joining this conversation. "
            "Read the recent messages and the latest message carefully. "
            "If there is a natural opportunity to chime in — a punchline to land, "
            "a topic you'd genuinely comment on, or a question you can answer in character — "
            "reply in the persona above. "
            f"Otherwise, output exactly one word: {SILENT}"
        )

    prompt = (
        f"{system}\n\n"
        f"--- Recent conversation (last {HISTORY_LIMIT} messages) ---\n"
        f"{history}\n"
        f"--- End of history ---\n\n"
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


intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
profile_text: str = ""


@client.event
async def on_ready() -> None:
    global profile_text
    profile_text = load_profile(PROFILE_PATH)
    print(f"[bot] logged in as {client.user} | profile: {len(profile_text)} chars")


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

    async with message.channel.typing():
        loop = asyncio.get_event_loop()
        reply = await loop.run_in_executor(
            None,
            lambda: call_gemini(profile_text, history, user_msg, GEMINI_MODEL, must_reply=mentioned),
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

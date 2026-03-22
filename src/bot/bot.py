"""Discord bot — event handlers only."""
import asyncio
import os
import random
import re
import sys
import time

import discord
from dotenv import load_dotenv

from .config import (
    DB_PATH,
    GEMINI_MODEL,
    GEMINI_SUPER_MODEL,
    GOSSIP_CHANNEL_ID,
    HISTORY_LIMIT,
    MEMBERS_DIR,
    RANDOM_REPLY_RATE,
    SILENT,
    SKILLS_DIR,
    URL_FETCH_LIMIT,
    URL_RE,
)
from .gemini import call_gemini, fetch_url_content
from .memory import (
    build_member_map,
    end_silence,
    format_recall_reply,
    get_member_history,
    is_silenced,
    log_bot_exchange,
    search_memory,
    search_sessions_for_recall,
    set_silence,
)
from .members import (
    get_profile_body,
    identify_members_via_llm,
    load_member_profiles,
    parse_member_headers,
)
from .skills import load_skill_body, rate_joke, route_tool_cli

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

member_map: dict[str, list[str]] = {}
member_profiles: dict[str, str] = {}
member_headers: dict[str, dict[str, list[str]]] = {}

_ALL_MEMBERS_RE = re.compile(
    r"哪些人|認識誰|大家|所有人|朋友們|你們|他們|每個人|都有誰|有哪些", re.IGNORECASE
)


@client.event
async def on_ready() -> None:
    global member_map, member_profiles, member_headers
    member_map = build_member_map(DB_PATH)
    member_profiles = load_member_profiles(MEMBERS_DIR)
    member_headers = parse_member_headers(member_profiles)
    alias_count = sum(len(v["aliases"]) for v in member_headers.values())
    print(
        f"[bot] logged in as {client.user} | "
        f"members: {len(member_map)} known | "
        f"member profiles: {len(member_profiles)} loaded | "
        f"friends skill: {len(member_headers)} members, {alias_count} aliases indexed"
    )


@client.event
async def on_message(message: discord.Message) -> None:
    print(f"[debug] on_message: {message.author} → {message.content!r}", flush=True)
    if message.author == client.user:
        return

    mentioned = client.user in message.mentions

    # Check if the bot's message was the last one (possible follow-up),
    # but only if the message doesn't mention someone else — that means it's directed elsewhere.
    # Also ignore if the bot's last message was more than 1 minute ago.
    mentions_others = any(u != client.user for u in message.mentions)
    last_was_bot = False
    if not mentions_others:
        async for prev in message.channel.history(limit=1, before=message):
            if prev.author == client.user:
                age = (message.created_at - prev.created_at).total_seconds()
                last_was_bot = age <= 60

    if not mentioned:
        if is_silenced(DB_PATH):
            return
        if not last_was_bot and random.random() > RANDOM_REPLY_RATE:
            return

    user_msg = message.content.replace(f"<@{client.user.id}>", "").strip()
    if not user_msg:
        return

    loop = asyncio.get_event_loop()

    # Fetch recent context
    recent_lines: list[str] = []
    async for msg in message.channel.history(limit=HISTORY_LIMIT + 1, before=message):
        author = msg.author.display_name
        content = msg.content.replace(f"<@{client.user.id}>", "@bot").strip()
        recent_lines.append(f"{author}: {content}")
    recent_lines.reverse()
    recent_context = "\n".join(recent_lines)

    # Tool dispatch
    if mentioned:
        tool_call = await loop.run_in_executor(
            None, lambda: route_tool_cli(user_msg, recent_context)
        )
        if tool_call:
            tool_name = tool_call["tool"]
            args = tool_call["args"]
            channel_id_str = GOSSIP_CHANNEL_ID or str(message.channel.id)
            guild_id_str = str(message.guild.id) if message.guild else ""

            if tool_name == "recall":
                results = await loop.run_in_executor(
                    None, lambda: search_sessions_for_recall(args.get("query", user_msg), DB_PATH)
                )
                await message.channel.send(format_recall_reply(results, guild_id_str, channel_id_str))
                return

            if tool_name == "silence":
                action = args.get("action", "start")
                if action == "end":
                    end_silence(DB_PATH)
                    await message.channel.send("好，我回來了 👋")
                else:
                    duration = int(args.get("duration", 5))
                    set_silence(DB_PATH, duration, message.author.display_name)
                    await message.channel.send(f"好，我靜默 {duration} 分鐘 🤫")
                return

            if tool_name == "joke-rating":
                await message.channel.send("[啟動笑話評分Skill]")
                skill_body = load_skill_body("joke-rating", SKILLS_DIR)
                rating = await loop.run_in_executor(
                    None, lambda: rate_joke(args.get("joke", user_msg), skill_body, recent_context, GEMINI_SUPER_MODEL or GEMINI_MODEL)
                )
                await message.channel.send(rating)
                return

    history = "\n".join(recent_lines) if recent_lines else "(no prior messages)"

    # Resolve sender identity
    sender_id = str(message.author.id)
    known_names = member_map.get(sender_id, [])
    sender_label = (
        f"{message.author.display_name} (also known as: {', '.join(known_names)})"
        if known_names else message.author.display_name
    )

    # Fetch sender history + topic memory in parallel
    search_context = f"{user_msg} {' '.join(recent_lines[-5:])}"
    t0 = time.monotonic()
    sender_history, topic_memory = await asyncio.gather(
        loop.run_in_executor(None, lambda: get_member_history(sender_id, DB_PATH)),
        loop.run_in_executor(None, lambda: search_memory(search_context, DB_PATH)),
    )
    print(f"[bot] memory fetch: {time.monotonic() - t0:.1f}s", flush=True)

    memory_parts: list[str] = []
    if sender_history:
        memory_parts.append(f"[Past messages from {sender_label}]\n{sender_history}")
    if topic_memory:
        memory_parts.append(f"[Topic-related past conversations]\n{topic_memory}")
    memory = "\n\n".join(memory_parts)

    sender_profile = member_profiles.get(sender_id, "")

    # Friends skill — identify mentioned members
    t_s1 = time.monotonic()
    matched_ids = await loop.run_in_executor(
        None,
        lambda: identify_members_via_llm(user_msg, member_headers, GEMINI_MODEL, exclude_id=sender_id),
    )
    ask_all = not matched_ids and bool(_ALL_MEMBERS_RE.search(user_msg))
    if ask_all:
        matched_ids = [aid for aid in member_headers if aid != sender_id and aid in member_profiles]
        print(f"[bot] friends skill: ask-all detected, injecting {len(matched_ids)} profiles", flush=True)
    elif matched_ids:
        print(f"[bot] friends skill stage1: {time.monotonic() - t_s1:.1f}s → {matched_ids}", flush=True)

    def _profile_with_identity(aid: str) -> str:
        info = member_headers[aid]
        aliases = [a for a in info["aliases"] if a != info["name"]]
        aka = f" (also known as: {', '.join(aliases)})" if aliases else ""
        return f"[Person: {info['name']}{aka}]\n{get_profile_body(member_profiles[aid])}"

    group_context = "\n\n".join(
        _profile_with_identity(aid)
        for aid in matched_ids
        if aid in member_profiles and aid in member_headers
    )

    # URL detection and fetching
    # mentioned → deep analysis (send skill trigger + thorough response)
    # casual    → fetch silently, chat naturally
    urls = URL_RE.findall(user_msg)[:URL_FETCH_LIMIT]
    if mentioned and not urls:
        async for hist_msg in message.channel.history(limit=10, before=message):
            found = URL_RE.findall(hist_msg.content)
            if found:
                urls = found[:URL_FETCH_LIMIT]
                break
    url_context = ""
    deep_url = False
    if urls:
        if mentioned:
            await message.channel.send("[啟動連結分析Skill]")
            deep_url = True
        fetch_results = await asyncio.gather(
            *[fetch_url_content(u) for u in urls], return_exceptions=True
        )
        parts = []
        for url, result in zip(urls, fetch_results):
            if isinstance(result, str) and result:
                parts.append(f"[URL: {url}]\n{result}")
        if parts:
            url_context = "\n\n".join(parts)
            print(f"[bot] fetched {len(parts)} URL(s), deep={deep_url}", flush=True)

    if memory or sender_profile:
        print(
            f"[bot] context: sender_profile={'yes' if sender_profile else 'no'}, "
            f"sender_history={'yes' if sender_history else 'no'}, "
            f"topic={topic_memory.count('[Session')} session(s)",
            flush=True,
        )

    async with message.channel.typing():
        t1 = time.monotonic()
        reply, input_prompt = await loop.run_in_executor(
            None,
            lambda: call_gemini(
                history, user_msg, GEMINI_MODEL,
                must_reply=mentioned, memory=memory,
                sender_label=sender_label, sender_profile=sender_profile,
                url_context=url_context, group_context=group_context,
                deep_url=deep_url,
            ),
        )
        print(f"[bot] gemini: {time.monotonic() - t1:.1f}s", flush=True)

    if reply == SILENT:
        print("[bot] chose to stay silent", flush=True)
        return

    await message.channel.send(reply)

    await loop.run_in_executor(
        None,
        lambda: log_bot_exchange(
            DB_PATH,
            str(message.channel.id),
            sender_id,
            sender_label,
            user_msg,
            reply,
            GEMINI_MODEL,
            input_prompt=input_prompt,
        ),
    )


def main() -> None:
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        print("[error] DISCORD_BOT_TOKEN not set", file=sys.stderr)
        sys.exit(1)
    client.run(token)


if __name__ == "__main__":
    main()

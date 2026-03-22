"""Gemini LLM calls and URL fetching."""
import os
import subprocess
from pathlib import Path

from playwright.async_api import async_playwright

from .config import (
    GEMINI_SYSTEM_MD_PATH,
    HISTORY_LIMIT,
    PROMPT_DIR,
    SILENT,
    URL_CHAR_LIMIT,
    URL_TIMEOUT_MS,
)


def _load_prompt(name: str) -> str:
    return (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8").strip()


async def fetch_url_content(url: str) -> str:
    """Use Playwright to fetch page title + body text. Returns empty string on failure."""
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=URL_TIMEOUT_MS)
            title = await page.title()
            body = await page.evaluate("document.body.innerText")
            await browser.close()
        content = body.strip()[:URL_CHAR_LIMIT]
        return f"Title: {title}\n{content}"
    except Exception as e:
        print(f"[bot] fetch failed {url}: {e}", flush=True)
        return ""


def call_gemini(
    history: str,
    user_msg: str,
    model: str | None,
    must_reply: bool = False,
    memory: str = "",
    sender_label: str = "",
    sender_profile: str = "",
    url_context: str = "",
    group_context: str = "",
    deep_url: bool = False,
) -> str:
    anti_injection = _load_prompt("anti_injection")
    no_ack = _load_prompt("no_ack")

    if deep_url:
        instruction = f"{anti_injection} {no_ack} {_load_prompt('reply_deep_url')}"
    elif must_reply:
        instruction = f"{anti_injection} {no_ack} {_load_prompt('reply_must')}"
    else:
        instruction = (
            f"{anti_injection} {no_ack} "
            + _load_prompt("reply_casual").format(SILENT=SILENT)
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
    group_block = (
        f"--- Profiles of group members mentioned in this message ---\n{group_context}\n"
        f"--- End of member profiles ---\n\n"
        if group_context else ""
    )
    url_block = (
        f"--- Content from shared links ---\n{url_context}\n"
        f"--- End of link content ---\n\n"
        if url_context else ""
    )

    sender_line = f"The person talking to you now: {sender_label}\n" if sender_label else ""
    prompt = (
        f"{sender_profile_block}"
        f"{memory_block}"
        f"{group_block}"
        f"{url_block}"
        f"--- Recent conversation (last {HISTORY_LIMIT} messages) ---\n"
        f"{history}\n"
        f"--- End of history ---\n\n"
        f"{sender_line}"
        f"Latest message: {user_msg}\n\n"
        f"{instruction}"
    )
    print(f"[bot] prompt size: {len(prompt)} chars", flush=True)
    cmd = ["gemini"]
    if model:
        cmd += ["--model", model]
    env = {**os.environ, "GEMINI_SYSTEM_MD": GEMINI_SYSTEM_MD_PATH}
    result = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd="/tmp", env=env)
    if result.returncode != 0:
        return f"[error] {result.stderr.strip()[:200]}", prompt
    return result.stdout.strip(), prompt

"""Skill loading, routing, and execution."""
import json
import re
import subprocess
from pathlib import Path

from .config import GEMINI_TOOL_MODEL, PROMPT_DIR, SKILLS_DIR


def load_skill_descriptors(skills_dir: Path) -> list[dict[str, str]]:
    """Scan skills/*/SKILL.md and return [{name, description}] from frontmatter only.

    Only skills with bypasses_llm: true are surfaced to the tool router.
    """
    descriptors: list[dict[str, str]] = []
    if not skills_dir.exists():
        return descriptors
    for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
        text = skill_md.read_text(encoding="utf-8")
        fm_match = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
        if not fm_match:
            continue
        fm = fm_match.group(1)
        name_m = re.search(r"^name:\s*(.+)$", fm, re.MULTILINE)
        desc_m = re.search(r"^description:\s*(.+)$", fm, re.MULTILINE)
        bypass_m = re.search(r"^\s*bypasses_llm:\s*(.+)$", fm, re.MULTILINE)
        if not name_m or not desc_m:
            continue
        if bypass_m and bypass_m.group(1).strip().lower() != "true":
            continue
        descriptors.append({
            "name": name_m.group(1).strip(),
            "description": desc_m.group(1).strip(),
        })
    return descriptors


def load_skill_body(skill_name: str, skills_dir: Path) -> str:
    """Return the body of a SKILL.md (everything after the frontmatter closing ---)."""
    skill_md = skills_dir / skill_name / "SKILL.md"
    if not skill_md.exists():
        return ""
    text = skill_md.read_text(encoding="utf-8")
    return re.sub(r"^---\n.*?\n---\n*", "", text, flags=re.DOTALL).strip()


def load_skill_section(skill_name: str, skills_dir: Path, section: str) -> str:
    """Return the content of a specific '## Section' from a SKILL.md body."""
    body = load_skill_body(skill_name, skills_dir)
    match = re.search(
        rf"^##\s+{re.escape(section)}\s*\n(.*?)(?=^##\s|\Z)",
        body,
        re.MULTILINE | re.DOTALL,
    )
    return match.group(1).strip() if match else ""


def route_tool_cli(user_msg: str, recent_context: str = "") -> dict | None:
    """Call GEMINI_TOOL_MODEL to decide which skill to invoke.

    Returns {tool, args} if a skill should be triggered, else None.
    """
    if not GEMINI_TOOL_MODEL:
        return None

    descriptors = load_skill_descriptors(SKILLS_DIR)
    if not descriptors:
        return None

    names = [d["name"] for d in descriptors]
    tool_lines = "\n".join(f"- {d['name']}: {d['description']}" for d in descriptors)
    context_block = (
        f"Recent conversation (use this to extract joke or query content if not in user message):\n"
        f"{recent_context}\n\n"
        if recent_context else ""
    )

    template = (PROMPT_DIR / "tool_router.md").read_text(encoding="utf-8")
    prompt = template.format(
        tool_lines=tool_lines,
        context_block=context_block,
        user_msg=user_msg,
    )

    cmd = ["gemini", "--model", GEMINI_TOOL_MODEL, "-p", prompt]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd="/tmp")
    if result.returncode != 0:
        print(f"[bot] route_tool error: {result.stderr.strip()[:200]}", flush=True)
        return None

    raw = result.stdout.strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.DOTALL).strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        print(f"[bot] route_tool: failed to parse JSON: {raw[:100]}", flush=True)
        return None

    tool_name = parsed.get("tool")
    if tool_name not in names:
        return None

    print(f"[bot] route_tool → {tool_name}", flush=True)
    return {"tool": tool_name, "args": parsed.get("args", {})}


def rate_joke(joke: str, skill_body: str, recent_context: str, model: str | None) -> str:
    """Call Gemini to rate a joke using the instruction from joke-rating SKILL.md body."""
    context_block = f"Recent conversation:\n{recent_context}\n\n" if recent_context else ""
    prompt = f"{skill_body}\n\n{context_block}笑話內容：{joke}"
    print(f"[bot] rate_joke prompt: {len(prompt)} chars", flush=True)
    cmd = ["gemini"]
    if model:
        cmd += ["--model", model]
    result = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd="/tmp")
    if result.returncode != 0:
        return f"[error] {result.stderr.strip()[:200]}"
    return result.stdout.strip()

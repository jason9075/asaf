"""Member profile loading and LLM-based identification."""
import re
import subprocess
from pathlib import Path

from .config import GEMINI_MODEL, SKILLS_DIR
from .skills import load_skill_section

_HOW_TO_INTERACT_RE = re.compile(r"\*\*How to interact.*", re.DOTALL | re.IGNORECASE)
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n*", re.DOTALL)


def load_member_profiles(members_dir: Path) -> dict[str, str]:
    """Load all data/members/<author_id>.md → {author_id: full_text}.

    Keeps frontmatter intact (used by parse_member_headers).
    Strips only the 'How to interact' section from the body.
    """
    profiles: dict[str, str] = {}
    if not members_dir.exists():
        return profiles
    for path in members_dir.glob("*.md"):
        author_id = path.stem
        text = path.read_text(encoding="utf-8").strip()
        profiles[author_id] = _HOW_TO_INTERACT_RE.sub("", text).strip()
    return profiles


def parse_member_headers(member_profiles: dict[str, str]) -> dict[str, dict[str, list[str]]]:
    """Extract YAML frontmatter from each profile → {author_id: {name, aliases}}.

    Expects frontmatter format:
        ---
        name: Display Name
        aliases:
          - alias1
          - alias2
        ---
    """
    headers: dict[str, dict[str, list[str]]] = {}
    for author_id, text in member_profiles.items():
        m = _FRONTMATTER_RE.match(text)
        if not m:
            continue
        fm = m.group(1)
        name_m = re.search(r"^name:\s*(.+)$", fm, re.MULTILINE)
        name = name_m.group(1).strip() if name_m else author_id
        aliases = re.findall(r"^\s+-\s+(.+)$", fm, re.MULTILINE)
        headers[author_id] = {"name": name, "aliases": aliases or [name]}
    return headers


def get_profile_body(text: str) -> str:
    """Return the body of a member profile (everything after the frontmatter)."""
    return _FRONTMATTER_RE.sub("", text).strip()


def identify_members_via_llm(
    user_msg: str,
    headers: dict[str, dict[str, list[str]]],
    model: str | None,
    exclude_id: str = "",
) -> list[str]:
    """Stage 1 — lightweight LLM call: which member IDs are referenced in user_msg?

    Returns list of matched author_ids, or [] if none.
    """
    candidates = {aid: info for aid, info in headers.items() if aid != exclude_id}
    if not candidates:
        return []

    member_lines = "\n".join(
        f"- {aid}: {info['name']}"
        + (f" (also known as: {', '.join(a for a in info['aliases'] if a != info['name'])})"
           if len(info["aliases"]) > 1 else "")
        for aid, info in candidates.items()
    )
    instruction = load_skill_section("friends", SKILLS_DIR, "Stage 1 Prompt")
    prompt = f"Group members:\n{member_lines}\n\nMessage: {user_msg}\n\n{instruction}"
    cmd = ["gemini"]
    if model:
        cmd += ["--model", model]
    result = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd="/tmp")
    if result.returncode != 0:
        return []
    output = result.stdout.strip()
    if not output or output.upper() == "NONE":
        return []
    return [line.strip() for line in output.splitlines() if line.strip() in candidates]

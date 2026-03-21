"""Centralised configuration — all env vars and path constants."""
import os
import re
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
_BOT_DIR = Path(__file__).parent
PROJECT_ROOT = _BOT_DIR.parent.parent

MEMBERS_DIR = Path(os.getenv("MEMBERS_DIR", "data/members"))
DB_PATH = Path(os.getenv("DB_PATH", "db/asaf.db"))
SKILLS_DIR = Path(os.getenv("SKILLS_DIR", "skills"))
PROMPT_DIR = _BOT_DIR / "prompt"
GEMINI_SYSTEM_MD_PATH = str(PROJECT_ROOT / ".gemini" / "system.md")

# ── Gemini ────────────────────────────────────────────────────────────────────
GEMINI_MODEL: str | None = os.getenv("GEMINI_MODEL", None)
GEMINI_TOOL_MODEL: str = os.getenv("GEMINI_TOOL_MODEL", "")
GEMINI_SUPER_MODEL: str | None = os.getenv("GEMINI_SUPER_MODEL", None)

# ── Bot behaviour ─────────────────────────────────────────────────────────────
HISTORY_LIMIT = int(os.getenv("HISTORY_LIMIT", "30"))
RANDOM_REPLY_RATE = float(os.getenv("RANDOM_REPLY_RATE", "0.05"))
GOSSIP_CHANNEL_ID = os.getenv("GOSSIP_CHANNEL_ID", "860932792283168789")

# ── Memory ────────────────────────────────────────────────────────────────────
MEMORY_SESSIONS = int(os.getenv("MEMORY_SESSIONS", "3"))
MEMORY_MSGS_PER_SESSION = int(os.getenv("MEMORY_MSGS_PER_SESSION", "5"))
RECALL_LIMIT = int(os.getenv("RECALL_LIMIT", "3"))

# ── URL fetching ──────────────────────────────────────────────────────────────
URL_RE = re.compile(r"https?://[^\s<>\"]+")
URL_FETCH_LIMIT = 2
URL_CHAR_LIMIT = int(os.getenv("URL_CHAR_LIMIT", "2000"))
URL_TIMEOUT_MS = 10_000

# ── Misc ──────────────────────────────────────────────────────────────────────
SILENT = "SILENT"

# ASAF

**A**nalyze, **S**egment, **A**nd **F**ragment — a data engineering pipeline that distills Discord group chat archives into per-person personality profiles, enabling a Discord bot to reply in each member's authentic voice, understand inside jokes, and reflect individual interests.

## How It Works

```
raw_json/gossip.json
        │
        ▼
   ingest.py          → data/messages.jsonl    (cleaned flat messages)
        │
        ▼
   segment.py         → data/sessions.jsonl    (time-split conversation sessions)
        │
        ▼
   analyze.py         → db/asaf.db             (per-session personality fragments via Gemini)
        │
        ▼
   synthesize.py      → data/profile.md        (master persona cards, one per person)
```

## Pipeline Stages

### Stage 1 — Ingest (`ingest.py`)

Parses the raw Discord JSON export and produces clean, flat messages.

- **Identity resolution:** `nickname` > `global_name` > `username`
- **Content sanitization:** `<@id>` → `@Name`, attachments → `[Sent an Image]`, URLs → `[URL: domain.com]`
- **Timestamp normalization:** any ISO 8601 → `YYYY-MM-DD HH:mm:ss` (UTC+8)
- Drops bots and system events (pins, joins, etc.)

### Stage 2 — Segment (`segment.py`)

Splits the message stream into discrete conversation sessions.

| Rule | Condition | Action |
|------|-----------|--------|
| **A — Silence** | gap > 4 hours | start new session |
| **B — Burst** | session reaches 100 messages | force-split |
| **C — Inertia** | first message of new session replies to previous session | annotate `parent_session_id` |

### Stage 3 — Analyze (`analyze.py`)

Filters sessions with ≥ 10 messages, batches them in groups of 10, and sends each batch to the Gemini CLI for personality extraction.

Each fragment captures four pillars:

| Pillar | Description |
|--------|-------------|
| `linguistic_style` | Vocabulary, sentence length, emoji/slang density, typing habits |
| `relational_role` | Listener / roaster / advice-giver / hype-man / topic-initiator |
| `emotional_baseline` | Cynical / optimistic / dry / anxious / enthusiastic |
| `knowledge_graph` | Recurring topics, shared jokes, niche interests |

Raw sessions and analysis results are persisted in `db/asaf.db` (SQLite). Runs are **resumable** — already-processed sessions are skipped automatically.

### Stage 4 — Synthesize (`synthesize.py`)

Aggregates all fragments by author into a single **persona card** per person, formatted as an LLM system prompt ready to power the Discord bot.

## Setup

```bash
nix develop        # enter dev shell (Python 3.12 + all deps)
cp .env.example .env
# add ANTHROPIC_API_KEY if using profile.py
# Gemini CLI uses its own cached credentials (gemini auth login)
```

## Usage

```bash
# Recommended flow
just ingest        # parse raw Discord JSON → messages.jsonl
just segment       # split into sessions → sessions.jsonl
just analyze       # batch-analyse via Gemini → db/asaf.db  (resumable)
just analyze | tee data/fragments.jsonl   # also stream results to file

# Dry-run (preview prompts, no API calls)
just analyze-dry

# Inspect sessions
just inspect       # sessions with message_count > 10 (default)
just inspect 50    # sessions with message_count > 50

# Utilities
just status        # row counts per stage
just clean         # remove data/ (keeps raw_json and db intact)
```

## Database Schema (`db/asaf.db`)

| Table | Description |
|-------|-------------|
| `sessions` | Raw session data (messages as JSON) |
| `batches` | Each Gemini call — prompt, raw response, status |
| `fragments` | Parsed personality fragment per session |

## File Layout

```
raw_json/
  gossip.json          Discord JSON export (do not commit if private)
data/
  messages.jsonl       Cleaned flat messages
  sessions.jsonl       Grouped sessions with metadata
  fragments.jsonl      Streamed fragment output (optional)
db/
  asaf.db              SQLite — sessions + batches + fragments
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Required only for `profile.py` (Claude-based profiling) |

Gemini CLI uses its own credential store — run `gemini` once interactively to authenticate.

# ASAF pipeline task runner
# Usage: just <target>

set dotenv-load := true

input   := "raw_json/gossip.json"
jsonl   := "data/messages.jsonl"
segs    := "data/sessions.jsonl"
profile := "data/profile.md"
target  := ""
model   := ""

pipeline := "src/pipeline"
bot      := "src/bot"

# List all available targets
default:
    @just --list

# ── Pipeline stages ──────────────────────────────────────────────────────────

# Stage 1: ingest raw Discord JSON → cleaned JSONL
ingest:
    mkdir -p data
    python {{pipeline}}/ingest.py --input {{input}} --output {{jsonl}}

# Stage 2: group messages into sessions
segment: ingest
    python {{pipeline}}/segment.py --input {{jsonl}} --output {{segs}}

# Stage 3 (Claude): extract personality fragments — just profile target=<author_id>
profile: segment
    python {{pipeline}}/profile.py --input {{segs}} --db db/asaf.db \
        $([ -n "{{target}}" ] && echo "--target {{target}}" || true) \
        $([ -n "{{model}}" ] && echo "--model {{model}}" || true)

# Dry-run: print prompts without calling API
profile-dry:
    python {{pipeline}}/profile.py --input {{segs}} --db db/asaf.db --dry-run

# Stage 4: synthesize fragments → master personality profile (uses gemini via analyze)
synthesize: analyze
    python {{pipeline}}/synthesize.py --output {{profile}}
    @echo "Profile written to {{profile}}"

# Run the full pipeline end-to-end
run: synthesize

# ── Analyze (gemini → SQLite) ─────────────────────────────────────────────────

# Stage 3 (Gemini): batch-analyse sessions → db/asaf.db (resumable)
analyze:
    python {{pipeline}}/analyze.py

# Dry-run: print prompts without calling gemini
analyze-dry:
    python {{pipeline}}/analyze.py --dry-run

# ── Inspect ───────────────────────────────────────────────────────────────────

# Show sessions with message_count > MIN (default 10)
# Usage: just inspect 20
inspect min="10":
    python {{pipeline}}/inspect_sessions.py --min {{min}}

# ── Member profiles ───────────────────────────────────────────────────────────

# Generate per-member personality profiles (data/members/<author_id>.md)
profile-members min="50":
    mkdir -p data/members
    python {{pipeline}}/profile_members.py --min-messages {{min}}

# Dry-run: show who qualifies and preview first prompt
profile-members-dry min="50":
    python {{pipeline}}/profile_members.py --min-messages {{min}} --dry-run

# ── Bot ───────────────────────────────────────────────────────────────────────

# Run Discord bot (WebSocket mode)
bot:
    python -m src.bot.bot

# Watch *.py and *.md for changes and auto-restart bot
watch:
    find src .gemini -name '*.py' -o -name '*.md' | grep -v __pycache__ | entr -r just bot

# ── Web Viewer ────────────────────────────────────────────────────────────────

# Start local web viewer at http://localhost:8000
viewer:
    python src/viewer.py --db db/asaf.db --port 8000

# ── Dev utilities ─────────────────────────────────────────────────────────────

# Type-check all Python files
typecheck:
    mypy src/ --strict

# Lint and auto-fix
lint:
    ruff check src/ --fix

# Inspect raw segments
segments:
    @cat {{segs}} | python -m json.tool | head -200

# ── Housekeeping ──────────────────────────────────────────────────────────────

# Remove all generated data (keeps raw_json intact)
clean:
    rm -rf data/

# Show row counts for each stage
status:
    @echo "=== messages (JSONL) ===" && wc -l < {{jsonl}} 2>/dev/null || echo "not generated"
    @echo "=== segments ===" && wc -l < {{segs}} 2>/dev/null || echo "not generated"
    @echo "=== profile_fragments (SQLite) ===" && \
        sqlite3 db/asaf.db "SELECT COUNT(*) FROM profile_fragments;" 2>/dev/null || echo "not generated"
    @echo "=== profile ===" && [ -f {{profile}} ] && echo "exists" || echo "not generated"

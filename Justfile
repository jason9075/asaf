# ASAF pipeline task runner
# Usage: just <target>

set dotenv-load := true

input  := "raw_json/gossip.json"
jsonl  := "data/messages.jsonl"
segs   := "data/sessions.jsonl"
frags  := "data/fragments.jsonl"
profile := "data/profile.md"

# List all available targets
default:
    @just --list

# ── Pipeline stages ──────────────────────────────────────────────────────────

# Stage 1: ingest raw Discord JSON → cleaned JSONL
ingest:
    mkdir -p data
    python ingest.py --input {{input}} --output {{jsonl}}

# Stage 2: group messages into sessions
segment: ingest
    python segment.py --input {{jsonl}} --output {{segs}}

# Stage 3: extract personality fragments via LLM
# Override target: just profile target=<author_id>
target := ""
profile: segment
    python profile.py --input {{segs}} --output {{frags}} \
        $([ -n "{{target}}" ] && echo "--target {{target}}" || true)

# Dry-run: print prompts without calling API
profile-dry:
    python profile.py --input {{segs}} --output {{frags}} --dry-run

# Stage 4: synthesize fragments → master personality profile
synthesize: profile
    python synthesize.py --input {{frags}} --output {{profile}}
    @echo "Profile written to {{profile}}"

# Run the full pipeline end-to-end
run: synthesize

# ── Inspect ───────────────────────────────────────────────────────────────────

# Show sessions with message_count > MIN (default 10)
# Usage: just inspect 20
inspect min="10":
    python inspect_sessions.py --min {{min}}

# ── Dev utilities ─────────────────────────────────────────────────────────────

# Type-check all Python files
typecheck:
    mypy *.py --strict

# Lint and auto-fix
lint:
    ruff check . --fix

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
    @echo "=== fragments ===" && wc -l < {{frags}} 2>/dev/null || echo "not generated"
    @echo "=== profile ===" && [ -f {{profile}} ] && echo "exists" || echo "not generated"

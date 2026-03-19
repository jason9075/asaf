# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**ASAF** is a data engineering pipeline that extracts personality profiles from Discord conversation archives using LLM analysis. It transforms raw Discord JSON exports (`gossip.json`) into synthesized personality profiles suitable for use as LLM system prompts.

## Architecture

Four-stage sequential pipeline:

1. **`ingest.py`** — Cleans raw Discord JSON → SQLite/JSONL flat messages
   - Identity resolution: `nickname` > `global_name` > `username`
   - Content sanitization: strip mentions (`<@!id>` → `@Username`), tag media as `[Sent an Image]`, shorten URLs to `[URL: domain.com]`
   - Normalize timestamps to ISO 8601 (`YYYY-MM-DD HH:mm:ss`)

2. **`segment.py`** — Groups messages into sessions by `session_id`
   - **Rule A (Silence):** 4-hour gap → new session
   - **Rule B (Burst):** max 100 messages per session (prevents context drift)
   - **Rule C (Inertia):** if the first message of a new session is a Discord reply to the last session, consider merging or adding a `parent_context` pointer
   - Use sliding window for very long conversations

3. **`profile.py`** — Sends each session to LLM → Personality Fragment per session
   - Four analysis pillars: Linguistic Style, Relational Role, Emotional Baseline, Knowledge Graph

4. **`synthesize.py`** — Aggregates all Fragments → Master Personality Profile (agent system prompt)

## Development Environment

```bash
nix develop       # Enter dev shell (flake.nix)
just <target>     # Run tasks (justfile)
```

Python 3.10+ with strict type hints required. Use `poetry2nix` or `mach-nix` for Python dependency management in Nix.

## Input Data

- `gossip.json` — Discord JSON export (source data, do not commit if it contains private conversations)

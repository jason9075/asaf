# Skill: Friends

## Purpose
Allow the bot to answer questions about specific group members with accurate knowledge
of their personality, instead of guessing or hallucinating.

## Trigger
Activate when the message appears to ask about or reference a specific group member,
e.g. "你認識XX嗎", "你覺得XX怎麼樣", "XX是什麼人", "XX最近在幹嘛".

## Two-Stage Flow

### Stage 1 — Member Identification (lightweight LLM call)
**Input:** user message + all member headers (name + aliases only)
**Task:** Identify which member IDs are referenced or asked about.
**Output:** matched author_id(s), one per line. If none, output exactly: `NONE`

Prompt template:
```
Group members:
- <author_id>: <name> (also known as: <aliases>)
...

Message: <user_msg>

Which member IDs are referenced or asked about in this message?
Output only the IDs, one per line. If none, output: NONE
```

### Stage 2 — Contextual Response (main LLM call)
**Input:** full personality profile of matched member(s) injected into `group_block`
**Position in prompt:** after memory_block, before url_block
**Header:** `--- Profiles of group members mentioned in this message ---`

## Member File Format
Each `data/members/<author_id>.md` has two sections separated by a YAML frontmatter block:

```markdown
---
name: Primary Display Name
aliases:
  - Alias One
  - Alias Two
---

**Linguistic Style**
...

**Interests & Topics**
...
```

The frontmatter (header) is used in Stage 1 for identification.
Everything after `---` is the profile body, injected in Stage 2.

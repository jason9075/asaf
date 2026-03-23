---
name: recall
description: Search historical conversation fragments and sessions as RAG context. Use when someone asks about a topic, person, event, or concept that may have been discussed before in the group chat — e.g. "你知道杜恩嗎", "大家有聊過PS5嗎", "之前討論過什麼遊戲".
license: MIT
compatibility: asaf
metadata:
  bypasses_llm: true
  output: context
  channel_id: "860932792283168789"
---

## What I do

- Search `profile_fragments` (SQLite) by primary_topic, entities, concepts, technologies via `json_extract`
- Fall back to raw `sessions` message content keyword search if fragments yield too few results
- Return relevant fragment summaries and message snippets as injected model context
- The bot then answers the question naturally using that context — no structured link output

## When to use me

Use when the question requires knowledge from past group chat history to answer well.

Examples:
- "你知道杜恩嗎？" → search for person name in entities
- "之前有沒有聊過量子力學" → search concepts/topics
- "大家有討論過PS5嗎" → search technologies
- "上次那個遊戲叫什麼名字" → search entities/topic
- 任何需要從過去對話中找資訊來回答的問題

## When NOT to use me

- User explicitly asks to "找對話" / "跳到那個訊息" → use recall's old jump-link behaviour instead
- Simple factual questions that don't depend on past chat (e.g. "今天幾號")

---
name: recall
description: Search historical SQLite sessions and return Discord jump links to relevant past conversations. Use when the user asks to find, look up, or search something from past chats.
license: MIT
compatibility: asaf
metadata:
  bypasses_llm: true
  output: structured
  channel_id: "860932792283168789"
---

## What I do

- Search historical conversation sessions in SQLite by keyword
- Return a formatted list of matching sessions with date, message previews, and Discord jump links
- Bypass the Gemini persona response — output is a structured Discord message

## When to use me

Use this when the user explicitly wants to retrieve or navigate to a past conversation.
Examples: "找找之前聊過NBA的對話", "幫我搜尋上次討論的話題", "之前有沒有聊過量子力學"

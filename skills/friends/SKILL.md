---
name: friends
description: Inject a group member's personality profile as context when the message asks about or references a specific person. Use when the user mentions someone by name or asks the bot's opinion about a member.
license: MIT
compatibility: asaf
metadata:
  bypasses_llm: false
  output: context-injection
---

## What I do

- Identify which group member(s) are referenced in the message (Stage 1 LLM call)
- Inject their full personality profile into the Gemini prompt as `group_context`
- Enable the bot to give accurate, profile-based opinions instead of hallucinating

## When to use me

Use this when the message asks about or references a specific group member by name or alias.
Examples: "你認識XX嗎", "你覺得XX怎麼樣", "XX是什麼人", "XX最近在幹嘛", "你跟XX熟嗎"

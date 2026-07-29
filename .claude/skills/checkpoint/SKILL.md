---
name: checkpoint
description: Save a handoff of the current session to .claude/CONTEXT.md so the next session can resume via /checkpoint-load. Use when the user invokes /checkpoint or asks to save session state before ending.
---

Save a handoff of the current session so a future session can resume it.

## What to do

1. From the current session context, draft the handoff using exactly these sections. Section headers stay in English (they are the file format); the content is written in the user's working language:

```
## Task
<One or two sentences: what this session is solving.>

## Done
<List of short bullet points: concrete actions, changes, findings. No filler.>

## Key decisions (and why)
<List: decision → short rationale. Only decisions actually made, not options considered.>

## Files touched
<List of paths relative to the repo root. If edited — mark "edited". If only read for context — mark "read".>

## Open questions
<What remains unresolved or is waiting on the user. If empty — write "none".>

## Next step
<One concrete action the next session starts with. Not a list — a single step.>
```

2. Show the full draft to the user in a code block. Do NOT write the file yet.

3. Ask (in the user's working language): "Write it as is, or edit?"

4. If the user asks for edits, apply them and show the updated draft again. Repeat until confirmed.

5. On confirmation, write the draft to `.claude/CONTEXT.md` (full overwrite — never append). Create the `.claude/` directory if it does not exist.

6. Confirm the write in one line, e.g. "Saved to `.claude/CONTEXT.md`. Restore next session with `/checkpoint-load`." (in the user's working language).

## Rules

- Full overwrite every time. Never append, never keep backups.
- Do not put long-lived facts (rules, architecture, preferences) here — those belong in auto-memory. This file is only "where I left off in this specific task".
- Keep it compact. Every section should be scannable in seconds.
- Never write the file without explicit user confirmation of the draft.

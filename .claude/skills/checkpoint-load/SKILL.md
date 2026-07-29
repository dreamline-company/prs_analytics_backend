---
name: checkpoint-load
description: Load the handoff saved by /checkpoint from .claude/CONTEXT.md and summarize it so the user can decide whether to resume. Use when the user invokes /checkpoint-load or asks to continue from the last session.
---

Restore the handoff saved by `/checkpoint` and wait for the user's next move.

## What to do

1. Read `.claude/CONTEXT.md`.

2. If the file does not exist: reply exactly "No saved checkpoint (`.claude/CONTEXT.md` not found)." — in the user's working language — and stop. Do nothing else.

3. If the file exists: output a 2–3 line summary in this shape (in the user's working language):
   - Line 1: the task (from the `## Task` section).
   - Line 2: the next step (from the `## Next step` section).
   - Line 3: "Continue? (I can show the full file if needed)."

4. Wait for the user. Do NOT start executing the next step on your own — the user must confirm, redirect, or ask for the full file.

## Rules

- Never auto-continue from the checkpoint. The load is passive: read, summarize, wait.
- Never modify `.claude/CONTEXT.md` in this skill. Writing is `/checkpoint`'s job.
- Backward compatibility: older checkpoints may use Russian section headers (`## Задача` = Task, `## Следующий шаг` = Next step). Treat them as equivalent when extracting the summary.
- If the summary can't be extracted cleanly (file is malformed or empty), print the raw contents and ask the user what to do.

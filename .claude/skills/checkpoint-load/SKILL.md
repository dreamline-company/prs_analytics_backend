---
name: checkpoint-load
description: Load the handoff saved by /checkpoint from .claude/CONTEXT.md and summarize it so the user can decide whether to resume. Use when the user invokes /checkpoint-load or asks to continue from the last session.
---

Restore the handoff saved by `/checkpoint` and wait for the user's next move.

## What to do

1. Read `.claude/CONTEXT.md`.

2. If the file does not exist: reply exactly "Нет сохранённого checkpoint (`.claude/CONTEXT.md` не найден)." and stop. Do nothing else.

3. If the file exists: output a 2–3 line summary in this shape:
   - Line 1: задача (из секции `## Задача`).
   - Line 2: следующий шаг (из секции `## Следующий шаг`).
   - Line 3: "Продолжаем? (могу открыть файл целиком, если нужно)."

4. Wait for the user. Do NOT start executing the "следующий шаг" on your own — the user must confirm, redirect, or ask for the full file.

## Rules

- Never auto-continue from the checkpoint. The load is passive: read, summarize, wait.
- Never modify `.claude/CONTEXT.md` in this skill. Writing is `/checkpoint`'s job.
- If the summary can't be extracted cleanly (file is malformed or empty), print the raw contents and ask the user what to do.

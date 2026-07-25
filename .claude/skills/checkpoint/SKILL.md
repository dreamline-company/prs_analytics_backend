---
name: checkpoint
description: Save a handoff of the current session to .claude/CONTEXT.md so the next session can resume via /checkpoint-load. Use when the user invokes /checkpoint or asks to save session state before ending.
---

Save a handoff of the current session so a future session can resume it.

## What to do

1. From the current session context, draft the handoff using exactly these sections (in Russian, matching the user's working language):

```
## Задача
<Одно-два предложения: что решаем в этой сессии.>

## Что сделано
<Список коротких пунктов: конкретные действия, изменения, находки. Без воды.>

## Ключевые решения (и почему)
<Список: решение → короткое обоснование. Только реально принятые решения, не рассмотренные варианты.>

## Затронутые файлы
<Список путей относительно корня репо. Если правили — пометь "правил". Если только читали для контекста — пометь "читал".>

## Открытые вопросы
<Что осталось нерешённым или ждёт ответа пользователя. Если пусто — напиши "нет".>

## Следующий шаг
<Одно конкретное действие, с которого начнётся следующая сессия. Не список — один шаг.>
```

2. Show the full draft to the user in a code block. Do NOT write the file yet.

3. Ask: "Записать как есть или поправить?"

4. If the user asks for edits, apply them and show the updated draft again. Repeat until confirmed.

5. On confirmation, write the draft to `.claude/CONTEXT.md` (full overwrite — never append). Create the `.claude/` directory if it does not exist.

6. Confirm the write in one line, e.g. "Записал в `.claude/CONTEXT.md`. Восстановить в след. сессии — `/checkpoint-load`."

## Rules

- Full overwrite every time. Never append, never keep backups.
- Do not put long-lived facts (rules, architecture, preferences) here — those belong in auto-memory. This file is only "где я остановился в этой конкретной задаче".
- Keep it compact. Every section should be scannable in seconds.
- Never write the file without explicit user confirmation of the draft.

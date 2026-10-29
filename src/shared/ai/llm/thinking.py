"""Метки рассуждения модели в тексте ответа.

Сервер модели не выносит рассуждение в отдельное поле (``reasoning_content``),
и даже без рассуждения модель иногда начинает ответ пустым блоком
«<|channel>thought <channel|>» — 29.10.2026 он дошёл до скважинного чата.
"""

import re

# Блок рассуждения целиком: Gemma — <|channel>…<channel|>, GLM/Qwen — <think>.
_BLOCKS = re.compile(r"<\|channel>.*?<channel\|>|<think>.*?</think>", re.DOTALL)
# Непарные метки, оставшиеся после блоков.
_MARKERS = re.compile(r"<\|channel>|<channel\|>|</?think>")


def strip_thinking(text: str) -> str:
    """Текст ответа без блоков рассуждения и служебных меток."""
    return _MARKERS.sub("", _BLOCKS.sub("", text)).strip()

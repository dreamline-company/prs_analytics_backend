"""Узкие tools скважинного ассистента: один tool — один вопрос.

Основное о скважине модель получает досье в системном промпте (см.
``dossier.py``); tools — для подробностей за период или по одному ремонту.
"""

from apps.ai_assistant.ai.well_chat_assistant.tools._common import (
    WellContextMissingError,
)
from apps.ai_assistant.ai.well_chat_assistant.tools.compensation import (
    get_compensation_contour,
)
from apps.ai_assistant.ai.well_chat_assistant.tools.incidents import (
    get_ai_conclusion,
    get_incidents,
)
from apps.ai_assistant.ai.well_chat_assistant.tools.repairs import (
    get_repair_analysis,
    get_well_mrp,
    list_repairs,
)
from apps.ai_assistant.ai.well_chat_assistant.tools.status import (
    get_abai_status_history,
)
from apps.ai_assistant.ai.well_chat_assistant.tools.telemetry import (
    get_drive_telemetry,
    get_rates_history,
)

WELL_CHAT_ASSISTANT_TOOLS = [
    get_incidents,
    get_ai_conclusion,
    get_drive_telemetry,
    get_rates_history,
    get_abai_status_history,
    list_repairs,
    get_well_mrp,
    get_repair_analysis,
    get_compensation_contour,
]

__all__ = ["WELL_CHAT_ASSISTANT_TOOLS", "WellContextMissingError"]

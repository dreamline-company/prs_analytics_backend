# ruff: noqa: TC002
"""Tool контура компенсации: потеря стоящей скважины и закреплённые доноры."""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from apps.ai_assistant.ai.well_chat_assistant.tools._common import get_well_id
from apps.compensation.dto.queries.compensation import GetCompensationContourQuery
from apps.compensation.use_cases.get_contour import GetCompensationContourUseCase
from shared.database.sql.setup import session_makers

# У крупной потери доноров бывает под три десятка — модели хватит главных.
DONORS_LIMIT = 10


@tool
async def get_compensation_contour(config: RunnableConfig) -> dict[str, Any]:
    """Контур компенсации скважины: стоит ли она (state: stopped — стоит и
    потеря считается, no_plan — стоит, но плана по нефти нет, working — не
    стоит), почему стоит, потеря (план Qн, т/сут), сколько компенсировано
    разгоном скважин-доноров и какие доноры закреплены (прирост, скорость
    было → станет, риск, расстояние). Пары на согласовании у технолога."""
    well_id = get_well_id(config)
    async with session_makers["app"]() as session:
        contour = await GetCompensationContourUseCase(session).execute(
            GetCompensationContourQuery(well_id=well_id),
        )
    data = contour.model_dump(mode="json")
    donors = data.get("main_donors") or []
    data["main_donors"] = donors[:DONORS_LIMIT]
    data["donors_total"] = len(donors)
    return data

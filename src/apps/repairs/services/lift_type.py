"""Способ добычи скважины ремонта и применимость динамограмм.

Динамограмма снимается только у штанговых насосов (ШГН): у ЭВН, УЭЦН и фонтана её нет, и
отсутствие динамограммы там — не недостаток ремонта (ни в ИИ-выводе, ни в KPI).
"""

from apps.wells.repositories import FundRevisionRepository


def dynamogram_applicable(lift_type: str | None) -> bool:
    """Снимается ли у такого способа добычи динамограмма.

    Тип неизвестен — считаем, что снимается (прежнее поведение). Комбинированные
    способы включают штанговый насос.
    """
    if not lift_type:
        return True
    name = lift_type.upper()
    return "ШГН" in name or name.startswith("КОМБИНИР")


async def get_lift_type(
    well_id: int | None,
    fund_revision_repo: FundRevisionRepository,
) -> str | None:
    """Способ добычи по последней ревизии фонда (ШГН / ЭВН / УЭЦН / Фонтан / …)."""
    if well_id is None:
        return None
    rows = await fund_revision_repo.get_latest_by_well_ids([well_id])
    row = rows.get(well_id)
    return row.lift_type if row is not None else None

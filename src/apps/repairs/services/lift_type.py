"""Способ добычи скважины ремонта и применимость динамограмм.

Динамограмма снимается только у штанговых насосов (ШГН): у ЭВН, УЭЦН, фонтана и
скважин без насоса её нет, и отсутствие динамограммы там — не недостаток ремонта
(ни в ИИ-выводе, ни в KPI).

Способ берётся из ABAI (копия ``emg.well_expl`` — периоды способа эксплуатации)
отдельно на начало и на конец ремонта: при ремонте способ меняют (ЭВН ↔ ШГН), и тогда
динамограмма есть только с одной стороны.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.repairs.models.repair import Repair
    from apps.wells.repositories import WellExplRepository

# Способы из справочника ABAI (emg.well_expl_type.name_ru), у которых нет
# штангового насоса с качалкой — динамограмма не снимается: электронасосы,
# винтовые насосы, фонтан и лифты без насоса, «Отсутствует» (насоса нет).
# Остальные (ШГН, САУ/ИСУ ШГН, ШГН+ЭЦН, Фонтан(ШГН), ЛШПН, «Не указано», …) и
# новые названия — динамограмма снимается (прежнее поведение).
_NO_DYNAMOGRAM = frozenset(
    name.upper()
    for name in (
        "ЭВН",
        "УЭЦН",
        "ЭЦВ",
        "Винтовой насос",
        "УЭВШН",
        "УШВН",
        "Фонтан",
        "Фонтан через насос",
        "Газлифт",
        "Плунжер лифт",
        "Свабирование",
        "НКТ с воронкой",
        "НКТ с муфтой",
        "НКТ с пакером",
        "СПТ с воронкой",
        "Пустая колонна",
        "Отсутствует",
    )
)
# Новый период способа ABAI открывает обычно в день окончания ремонта, но бывает
# и на несколько дней позже (9.11.2026: 82 и 38 из 146 смен способа у ремонтов).
_AFTER_REPAIR_TAIL = timedelta(days=7)


def dynamogram_applicable(lift_type: str | None) -> bool:
    """Снимается ли у такого способа добычи динамограмма.

    Способ неизвестен или не из списка — считаем, что снимается (прежнее поведение).
    """
    if not lift_type:
        return True
    return lift_type.strip().upper() not in _NO_DYNAMOGRAM


@dataclass(frozen=True, slots=True)
class RepairLiftTypes:
    """Способ добычи до ремонта и после него (``None`` — неизвестен)."""

    before: str | None = None
    after: str | None = None

    @property
    def dynamogram_before_applicable(self) -> bool:
        return dynamogram_applicable(self.before)

    @property
    def dynamogram_after_applicable(self) -> bool:
        return dynamogram_applicable(self.after)


def _lift_on(periods: list[tuple[date | None, str | None]], day: date) -> str | None:
    """Способ последнего периода, начавшегося не позже ``day``."""
    name = None
    for dbeg, period_name in periods:
        if dbeg is None or dbeg <= day:
            name = period_name
    return name


async def get_repair_lift_types(
    repair: Repair,
    well_expl_repo: WellExplRepository,
    *,
    today: date,
) -> RepairLiftTypes:
    """Способ добычи накануне начала ремонта и после его окончания.

    «До» — накануне начала: период, открытый в первый день ремонта, — уже его
    результат. «После» — с запасом в несколько дней после окончания: ABAI
    открывает новый период не всегда в день окончания. У незавершённого
    ремонта «после» — на сегодня.
    """
    if repair.abai_well_id is None:
        return RepairLiftTypes()
    periods = await well_expl_repo.list_named_periods_by_abai_well_id(
        repair.abai_well_id,
    )
    after_day = (
        repair.end_time.date() + _AFTER_REPAIR_TAIL
        if repair.end_time is not None
        else today
    )
    return RepairLiftTypes(
        before=_lift_on(periods, repair.start_time.date() - timedelta(days=1)),
        after=_lift_on(periods, after_day),
    )

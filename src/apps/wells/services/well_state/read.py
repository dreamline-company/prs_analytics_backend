"""Чтение итогового состояния для матрицы и карточки.

Только открытые строки ``wells_well_state`` и строки прогонов — тяжёлых
вычислений в запросе нет. Скважина без строки (новая, до первого прогона) —
``None``.
"""

from collections.abc import Sequence
from datetime import datetime, timedelta

from apps.wells.dto.internal.well_state import WellStateDTO
from apps.wells.models.well_state import STATE_TITLES_RU, WellState, WellStateRun
from apps.wells.repositories import WellStateRepository, WellStateRunRepository
from core.settings import get_settings

# Таска идёт раз в 5 минут: 15 минут — три пропущенных прогона подряд.
STALE_AFTER = timedelta(minutes=15)


def _fmt(moment: datetime) -> str:
    return moment.strftime("%d.%m %H:%M")


def build_well_state(
    row: WellState,
    runs: dict[int, WellStateRun],
    now: datetime,
) -> WellStateDTO:
    stale_reason = None
    run = runs.get(row.abai_ngdu_id)
    if run is None or now - run.last_run_at > STALE_AFTER:
        last = run.last_run_at if run is not None else row.updated_at
        stale_reason = f"Состояние не обновлялось с {_fmt(last)}"
    else:
        drive = (row.evidence or {}).get("drive") or {}
        sdmo_run = runs.get(drive.get("abai_ngdu_id"))
        if sdmo_run is not None and sdmo_run.sdmo_outage_since is not None:
            stale_reason = (
                f"Нет данных СДМО по НГДУ с {_fmt(sdmo_run.sdmo_outage_since)}"
            )
    return WellStateDTO(
        code=row.state,
        title_ru=STATE_TITLES_RU.get(row.state, row.state),
        reason_ru=row.reason_ru,
        since=row.started_at,
        source=row.source,
        stale=stale_reason is not None,
        stale_reason=stale_reason,
    )


class WellStateReadService:
    def __init__(
        self,
        state_repository: WellStateRepository,
        run_repository: WellStateRunRepository,
    ) -> None:
        self.state_repository = state_repository
        self.run_repository = run_repository

    async def get_for_wells(
        self,
        well_ids: Sequence[int],
        *,
        now: datetime | None = None,
    ) -> dict[int, WellStateDTO | None]:
        """Ключ на каждую запрошенную скважину; ``None`` — строки нет."""
        rows = await self.state_repository.list_open_by_well_ids(well_ids)
        runs = await self.run_repository.get_map() if rows else {}
        now = now or datetime.now(get_settings().ZONE_INFO).replace(tzinfo=None)
        return {
            well_id: (
                build_well_state(rows[well_id], runs, now) if well_id in rows else None
            )
            for well_id in well_ids
        }

    async def get_for_well(self, well_id: int) -> WellStateDTO | None:
        return (await self.get_for_wells([well_id]))[well_id]

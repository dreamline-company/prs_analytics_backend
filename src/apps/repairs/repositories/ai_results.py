import re
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import ColumnElement, Integer, and_, cast, func, or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert

from apps.repairs.dto.internal.repositories.ai_results import (
    CreateRepairAIAnalysisDTO,
    CreateRepairDynamogramAIResultDTO,
    CreateRepairSPOAIResultDTO,
    UpdateRepairAIAnalysisDTO,
    UpdateRepairDynamogramAIResultDTO,
    UpdateRepairSPOAIResultDTO,
)
from apps.repairs.models.analytics import (
    AI_STATUS_COMPLETED,
    AI_STATUS_FAILED,
    RepairAIAnalysis,
    RepairDynamogramAIResult,
    RepairSPOAIResult,
)
from apps.wells.models.dynamogram import Dynamogram
from apps.wells.models.well import Well
from shared.repository.sqlalchemy import AsyncAlchemyRepository, QuerySpec

_VERSION_NUMBER_RE = re.compile(r"[0-9]+")
# Пространство advisory-замков разбора динамограмм: pg_advisory_xact_lock(int, int).
_DYNAMOGRAM_LOCK_NAMESPACE = 7301


def version_number(version: str | None) -> int:
    """Порядок версий промпта — первое число: 'v3' → 3, 'v12' → 12;
    ``None`` и версия без цифр — 0."""
    match = _VERSION_NUMBER_RE.search(version or "")
    return int(match.group()) if match else 0


def _version_number_sql(column: ColumnElement) -> ColumnElement:
    """То же, что ``version_number``, в SQL."""
    digits = func.substring(column, _VERSION_NUMBER_RE.pattern)
    return func.coalesce(cast(digits, Integer), 0)


class RepairDynamogramAIResultRepository(
    AsyncAlchemyRepository[
        CreateRepairDynamogramAIResultDTO,
        UpdateRepairDynamogramAIResultDTO,
        RepairDynamogramAIResult,
    ],
):
    model = RepairDynamogramAIResult

    async def get_by_dynamogram_id(
        self,
        dynamogram_id: int,
    ) -> RepairDynamogramAIResult | None:
        return await self.get_one(
            QuerySpec(
                filters=(RepairDynamogramAIResult.dynamogram_id == dynamogram_id,),
            ),
        )

    async def list_by_dynamogram_ids(
        self,
        dynamogram_ids: Sequence[int],
    ) -> Sequence[RepairDynamogramAIResult]:
        if not dynamogram_ids:
            return ()
        return await self.get_list(
            QuerySpec(
                filters=(RepairDynamogramAIResult.dynamogram_id.in_(dynamogram_ids),),
            ),
        )

    async def upsert(
        self,
        data: CreateRepairDynamogramAIResultDTO,
    ) -> RepairDynamogramAIResult:
        """Вставить или перезаписать разбор динамограммы; вернуть то, что в
        итоге лежит в таблице.

        Писателей двое — аналитика ремонта и проход по фонду, — и к одной
        динамограмме они могут прийти одновременно: конфликт по
        ``dynamogram_id`` решается здесь, а не исключением. Не перезаписываются:
        строка более новой версии промпта (старый код не откатит её, начиная с
        v3 — у кода v2 этой защиты нет) и готовый разбор той же версии упавшим
        (сбой LLM у второго писателя не затрёт результат первого).
        """
        table = RepairDynamogramAIResult
        values = data.model_dump()
        stmt = pg_insert(table).values(**values)
        old_version = _version_number_sql(table.prompt_version)
        new_version = _version_number_sql(stmt.excluded.prompt_version)
        stmt = (
            stmt.on_conflict_do_update(
                index_elements=["dynamogram_id"],
                set_={
                    **{
                        key: stmt.excluded[key]
                        for key in values
                        if key != "dynamogram_id"
                    },
                    "updated_at": func.now(),
                },
                where=or_(
                    old_version < new_version,
                    and_(
                        old_version == new_version,
                        or_(
                            table.status != AI_STATUS_COMPLETED,
                            stmt.excluded.status == AI_STATUS_COMPLETED,
                        ),
                    ),
                ),
            )
            .returning(table)
            .execution_options(populate_existing=True)
        )
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        if row is not None:
            return row
        return await self.get_by_dynamogram_id(data.dynamogram_id)

    async def lock_dynamogram(self, dynamogram_id: int, *, wait: bool) -> bool:
        """Транзакционный замок разбора динамограммы (до commit/rollback).

        ``wait=False`` — не ждать: ``False``, если замок держит другой
        писатель.
        """
        args = (
            cast(_DYNAMOGRAM_LOCK_NAMESPACE, Integer),
            cast(dynamogram_id, Integer),
        )
        if wait:
            await self.session.execute(select(func.pg_advisory_xact_lock(*args)))
            return True
        result = await self.session.execute(
            select(func.pg_try_advisory_xact_lock(*args)),
        )
        return bool(result.scalar_one())

    async def list_dynamograms_to_analyze(  # noqa: PLR0913
        self,
        *,
        prompt_version: str,
        failed_before: datetime,
        rejected_error_prefix: str,
        after: tuple[datetime, int] | None,
        limit: int,
        well_id: int | None = None,
    ) -> list[tuple[Dynamogram, int | None]]:
        """Динамограммы без актуального разбора, свежие сверху: (динамограмма,
        ABAI id скважины).

        Условие — то же, что ``needs_analysis`` в анализаторе: разбора нет,
        он старой версии, не завершён, или упал не на входном файле и давно.
        ``after`` — курсор (snapshot_time, id) последней строки прошлой
        страницы: страница не возвращается заново, даже если её строки не
        изменились (упали, не прошли защиту версии).
        """
        result = RepairDynamogramAIResult
        current = version_number(prompt_version)
        row_version = _version_number_sql(result.prompt_version)
        stale = or_(
            result.id.is_(None),
            row_version < current,
            and_(
                row_version == current,
                or_(
                    result.status.not_in((AI_STATUS_COMPLETED, AI_STATUS_FAILED)),
                    and_(
                        result.status == AI_STATUS_FAILED,
                        or_(
                            result.processed_at.is_(None),
                            result.processed_at < failed_before,
                        ),
                        or_(
                            result.error.is_(None),
                            result.error.not_like(f"{rejected_error_prefix}%"),
                        ),
                    ),
                ),
            ),
        )
        stmt = (
            select(Dynamogram, Well.abai_id)
            .join(Well, Well.id == Dynamogram.well_id)
            .outerjoin(result, result.dynamogram_id == Dynamogram.id)
            .where(stale)
            .order_by(Dynamogram.snapshot_time.desc(), Dynamogram.id.desc())
            .limit(limit)
        )
        if after is not None:
            stmt = stmt.where(
                tuple_(Dynamogram.snapshot_time, Dynamogram.id) < tuple_(*after),
            )
        if well_id is not None:
            stmt = stmt.where(Dynamogram.well_id == well_id)
        rows = await self.session.execute(stmt)
        return [(dynamogram, abai_id) for dynamogram, abai_id in rows.all()]


class RepairSPOAIResultRepository(
    AsyncAlchemyRepository[
        CreateRepairSPOAIResultDTO,
        UpdateRepairSPOAIResultDTO,
        RepairSPOAIResult,
    ],
):
    model = RepairSPOAIResult

    async def get_by_spo_id(self, spo_id: int) -> RepairSPOAIResult | None:
        return await self.get_one(
            QuerySpec(filters=(RepairSPOAIResult.spo_id == spo_id,)),
        )

    async def list_by_spo_ids(
        self,
        spo_ids: Sequence[int],
    ) -> Sequence[RepairSPOAIResult]:
        if not spo_ids:
            return ()
        return await self.get_list(
            QuerySpec(
                filters=(RepairSPOAIResult.spo_id.in_(spo_ids),),
            ),
        )

    async def update_by_spo_id(
        self,
        spo_id: int,
        data: UpdateRepairSPOAIResultDTO,
    ) -> RepairSPOAIResult:
        return await self.update(
            data=data,
            filters=(RepairSPOAIResult.spo_id == spo_id,),
        )


class RepairAIAnalysisRepository(
    AsyncAlchemyRepository[
        CreateRepairAIAnalysisDTO,
        UpdateRepairAIAnalysisDTO,
        RepairAIAnalysis,
    ],
):
    model = RepairAIAnalysis

    async def get_by_analytics_id(
        self,
        analytics_id: int,
    ) -> RepairAIAnalysis | None:
        return await self.get_one(
            QuerySpec(filters=(RepairAIAnalysis.analytics_id == analytics_id,)),
        )

    async def update_by_analytics_id(
        self,
        analytics_id: int,
        data: UpdateRepairAIAnalysisDTO,
    ) -> RepairAIAnalysis:
        return await self.update(
            data=data,
            filters=(RepairAIAnalysis.analytics_id == analytics_id,),
        )

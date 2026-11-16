"""Сверка зеркал ABAI с источником для таблиц, которые читаются целиком.

За прогон источник читается полностью и сравнивается с копией по ``abai_id``:
новые строки добавляются, изменённые переписываются целиком, исчезнувшие
удаляются. Так копия ловит и правки закрытых периодов, и удаления в источнике.

Таблицы передаются в порядке зависимостей (справочник → привязка): добавления
и правки идут в этом порядке, удаления — в обратном, чтобы внешние ключи не
повисли ни на одном шаге. Всё пишется в одной транзакции.

Если источник вдруг отдал меньше половины строк копии (пустая таблица после
перезаливки, пропавшие права), прогон прерывается целиком: иначе сверка
стёрла бы зеркало вместе со всеми зависимыми таблицами. Так же прерывается
прогон, где удалений в таблице больше ``MAX_DELETE`` / ``MAX_DELETE_SHARE``:
чтение идёт порциями, и оборванная порция иначе удалила бы часть зеркала на
сутки. Настоящую массовую правку в ABAI записывает ручной прогон с ``force``.
Обе проверки срабатывают до первой записи.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from core import get_logger
from shared.dto.repositories import RepositoryDTO
from shared.errors import AppError

logger = get_logger(__name__)

# Источник короче этой доли копии — считаем его сломанным и не сверяем.
MIN_SOURCE_SHARE = 0.5
# Больше стольких удалений в таблице за прогон — сбой чтения, не правки.
MAX_DELETE = 50
MAX_DELETE_SHARE = 0.05


class SourceShrunkError(AppError):
    message = "ABAI returned far fewer rows than the mirror holds"
    code = "abai_mirror_source_shrunk"


class MirrorRepository[DTOT: RepositoryDTO](Protocol):
    async def list_all(self) -> Sequence[Any]: ...

    async def bulk_create(self, data: Sequence[DTOT]) -> None: ...

    async def update_by_abai_id(self, data: DTOT) -> None: ...

    async def delete_by_abai_ids(self, abai_ids: Sequence[int]) -> None: ...


@dataclass(frozen=True, slots=True)
class MirrorTable[DTOT: RepositoryDTO]:
    """Одна таблица зеркала и её строки из источника.

    ``source_count`` — строк в источнике до отбора; разница с ``rows`` —
    пропущенные (ссылка на скважину не из ``wells_well`` и т. п.).
    """

    name: str
    repo: MirrorRepository[DTOT]
    rows: Sequence[DTOT]
    source_count: int


@dataclass(frozen=True, slots=True)
class SyncPlan[DTOT: RepositoryDTO]:
    """Что сделать с одной таблицей копии, чтобы она совпала с источником."""

    create: list[DTOT] = field(default_factory=list)
    update: list[DTOT] = field(default_factory=list)
    delete: list[int] = field(default_factory=list)


@dataclass(slots=True)
class TableStats:
    source: int = 0
    skipped: int = 0
    created: int = 0
    updated: int = 0
    deleted: int = 0


def plan_sync[DTOT: RepositoryDTO](
    source: Iterable[DTOT],
    local: Iterable[Any],
) -> SyncPlan[DTOT]:
    """Сравнить строки источника с копией по ``abai_id``.

    Строка копии считается изменённой, если хоть одно поле DTO отличается от
    одноимённого атрибута модели.
    """
    local_by_id = {row.abai_id: row for row in local}
    plan: SyncPlan[DTOT] = SyncPlan()
    seen: set[int] = set()

    for dto in source:
        seen.add(dto.abai_id)
        current = local_by_id.get(dto.abai_id)
        if current is None:
            plan.create.append(dto)
        elif any(
            getattr(current, name) != value for name, value in dto.model_dump().items()
        ):
            plan.update.append(dto)

    plan.delete.extend(sorted(set(local_by_id) - seen))
    return plan


async def sync_mirror(
    session: AsyncSession,
    tables: Sequence[MirrorTable[Any]],
    *,
    label: str,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, TableStats]:
    """Привести таблицы зеркала к источнику; ``label`` — имя зеркала в журнале.

    Повторы ``abai_id`` в источнике (в FDW-таблицах ABAI нет первичного ключа:
    в ``emg.geo`` есть полные дубли строк) отбрасываются, остаётся одна строка;
    в статистике они попадают в «пропущено». Источник читается ORM-моделями,
    а identity map схлопывает строки с одним id в один объект: если копии
    расходятся, в зеркало попадёт та, что пришла первой, и отличить их здесь
    нельзя.
    """
    rows = [_unique_by_abai_id(label, table) for table in tables]
    mirrors = [await table.repo.list_all() for table in tables]
    for table, unique, mirror in zip(tables, rows, mirrors, strict=True):
        _check_source(label, table.name, source=len(unique), mirror=len(mirror))
    plans = [
        plan_sync(unique, mirror) for unique, mirror in zip(rows, mirrors, strict=True)
    ]
    if not force:
        for table, mirror, plan in zip(tables, mirrors, plans, strict=True):
            _check_deletes(
                label,
                table.name,
                deleted=len(plan.delete),
                mirror=len(mirror),
            )

    stats: dict[str, TableStats] = {}
    try:
        for table, unique, plan in zip(tables, rows, plans, strict=True):
            stats[table.name] = TableStats(
                source=table.source_count,
                skipped=table.source_count - len(unique),
                created=len(plan.create),
                updated=len(plan.update),
                deleted=len(plan.delete),
            )
            await table.repo.bulk_create(plan.create)
            for dto in plan.update:
                await table.repo.update_by_abai_id(dto)
        for table, plan in reversed(list(zip(tables, plans, strict=True))):
            await table.repo.delete_by_abai_ids(plan.delete)

        if dry_run:
            await session.rollback()
        else:
            await session.commit()
    except Exception:
        await session.rollback()
        logger.exception("Error while syncing %s", label)
        raise

    logger.info(
        "%s sync finished%s: %s",
        label,
        " (dry run)" if dry_run else "",
        ", ".join(
            f"{name} +{s.created} ~{s.updated} -{s.deleted} skip {s.skipped}"
            for name, s in stats.items()
        ),
    )
    return stats


def print_summary(stats: dict[str, TableStats], *, dry_run: bool) -> None:
    """Итог ручного прогона для CLI."""
    print("DRY RUN — ничего не записано" if dry_run else "Записано")  # noqa: T201
    width = max((len(name) for name in stats), default=0)
    for name, s in stats.items():
        print(  # noqa: T201
            f"{name:{width}} источник {s.source:6}  пропущено {s.skipped:5}  "
            f"добавлено {s.created:6}  изменено {s.updated:5}  удалено {s.deleted:5}",
        )


def _unique_by_abai_id[DTOT: RepositoryDTO](
    label: str,
    table: MirrorTable[DTOT],
) -> list[DTOT]:
    unique: dict[int, DTOT] = {}
    for dto in table.rows:
        unique.setdefault(dto.abai_id, dto)
    duplicates = len(table.rows) - len(unique)
    if duplicates:
        # Известные дубли emg.geo повторяются каждый день — не предупреждение.
        logger.info(
            "%s sync: %s has %s duplicate ids in ABAI, kept one row of each",
            label,
            table.name,
            duplicates,
        )
    return list(unique.values())


def _check_source(label: str, name: str, *, source: int, mirror: int) -> None:
    if source < MIN_SOURCE_SHARE * mirror:
        logger.error(
            "%s sync aborted: %s has %s rows in ABAI, %s in the mirror",
            label,
            name,
            source,
            mirror,
        )
        raise SourceShrunkError


def _check_deletes(label: str, name: str, *, deleted: int, mirror: int) -> None:
    if deleted > max(MAX_DELETE, mirror * MAX_DELETE_SHARE):
        logger.error(
            "%s sync aborted: %s would delete %s of %s rows "
            "(run with --force if ABAI really changed that much)",
            label,
            name,
            deleted,
            mirror,
        )
        raise SourceShrunkError

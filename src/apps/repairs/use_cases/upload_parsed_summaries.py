"""Приём распарсенных суточных сводок ПРС.

Загрузка идёт помесячными файлами (лист = сутки, строка = скважина, две смены
на строку), поэтому сценарий рассчитан на батч в тысячи записей и на повторную
заливку того же файла:

* сводка идентифицируется ключом «скважина × сутки × смена» и переписывается
  при повторной отправке;
* дубли внутри одного батча схлопываются — побеждает последняя запись;
* строки с неизвестными скважинами не роняют весь батч, а возвращаются
  вызывающей стороне списком.
"""

from collections import defaultdict
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from apps.org.models.brigade import UniqueBrigade
from apps.org.repositories import UniqueBrigadeRepository
from apps.repairs.dto.internal.repositories.brigade import CreateRepairBrigadeDTO
from apps.repairs.dto.internal.repositories.reports import CreateRepairSummaryDTO
from apps.repairs.dto.internal.summary import UploadParsedSummariesResultDTO
from apps.repairs.dto.requests.summaries import (
    UploadParsedSummariesListDTO,
    UploadParsedSummaryDTO,
)
from apps.repairs.models.repair import Repair
from apps.repairs.repositories.brigade import RepairBrigadeRepository
from apps.repairs.repositories.repair import RepairRepository
from apps.repairs.repositories.reports import RepairSummaryRepository, SummaryKey
from apps.wells.models.well import Well
from apps.wells.repositories import WellRepository
from core import get_logger
from shared.errors import HttpError

logger = get_logger(__name__)


class WellsNotFoundError(HttpError):
    message = "None of the wells referenced by summaries were found."
    code = "wells_not_found"
    status_code = status.HTTP_400_BAD_REQUEST


class UploadParsedSummariesUseCase:
    def __init__(  # noqa: PLR0913
        self,
        session: AsyncSession,
        well_repository: WellRepository,
        repair_summary_repository: RepairSummaryRepository,
        repair_repository: RepairRepository,
        repair_brigade_repository: RepairBrigadeRepository,
        unique_brigade_repository: UniqueBrigadeRepository,
    ) -> None:
        self.session = session
        self.well_repository = well_repository
        self.repair_summary_repository = repair_summary_repository
        self.repair_repository = repair_repository
        self.repair_brigade_repository = repair_brigade_repository
        self.unique_brigade_repository = unique_brigade_repository

    async def execute(
        self,
        payload: UploadParsedSummariesListDTO,
    ) -> UploadParsedSummariesResultDTO:
        summaries = payload.summaries
        if not summaries:
            return UploadParsedSummariesResultDTO(received=0)

        wells_by_name, unknown_wells = await self._resolve_wells(summaries)

        accepted = [s for s in summaries if s.well_name in wells_by_name]
        skipped_unknown_well = len(summaries) - len(accepted)
        if not accepted:
            raise WellsNotFoundError(details={"well_names": sorted(unknown_wells)})

        dtos_by_key = self._build_create_dtos(accepted, wells_by_name)
        existing_keys = await self.repair_summary_repository.list_existing_keys(
            dtos_by_key.keys(),
        )
        await self.repair_summary_repository.bulk_upsert(list(dtos_by_key.values()))

        linked_brigades = await self._link_brigades(accepted, wells_by_name)

        await self.session.commit()

        result = UploadParsedSummariesResultDTO(
            received=len(summaries),
            created=len(dtos_by_key.keys() - existing_keys),
            updated=len(dtos_by_key.keys() & existing_keys),
            deduplicated=len(accepted) - len(dtos_by_key),
            skipped_unknown_well=skipped_unknown_well,
            unknown_wells=sorted(unknown_wells),
            linked_brigades=linked_brigades,
        )
        logger.info(
            "Parsed summaries upload: received=%s created=%s updated=%s "
            "deduplicated=%s skipped_unknown_well=%s linked_brigades=%s",
            result.received,
            result.created,
            result.updated,
            result.deduplicated,
            result.skipped_unknown_well,
            result.linked_brigades,
        )
        return result

    async def _resolve_wells(
        self,
        summaries: list[UploadParsedSummaryDTO],
    ) -> tuple[dict[str, Well], set[str]]:
        names: set[str] = set()
        for summary in summaries:
            names.add(summary.well_name)
            if summary.second_well_name:
                names.add(summary.second_well_name)

        wells = await self.well_repository.list_by_names(list(names))
        wells_by_name = {well.name: well for well in wells}

        unknown = names - wells_by_name.keys()
        if unknown:
            logger.warning(
                "Unknown wells in parsed summaries (%s): %s",
                len(unknown),
                sorted(unknown)[:20],
            )
        return wells_by_name, unknown

    @staticmethod
    def _build_create_dtos(
        summaries: list[UploadParsedSummaryDTO],
        wells_by_name: dict[str, Well],
    ) -> dict[SummaryKey, CreateRepairSummaryDTO]:
        """Схлопнуть батч до одной записи на ключ «скважина × сутки × смена».

        Внутри месячного файла одна и та же смена может встретиться дважды
        (повтор строки, склейка листов) — берём последнюю: она свежее.
        """
        result: dict[SummaryKey, CreateRepairSummaryDTO] = {}
        for summary in summaries:
            well_id = wells_by_name[summary.well_name].id
            second_well = (
                wells_by_name.get(summary.second_well_name)
                if summary.second_well_name
                else None
            )

            key: SummaryKey = (well_id, summary.start_date, summary.shift_type_number)
            result[key] = CreateRepairSummaryDTO(
                well_id=well_id,
                second_well_id=second_well.id if second_well else None,
                date=summary.start_date,
                brigade_number=summary.brigade_number,
                pump_type=summary.pump_type,
                shift_type_number=summary.shift_type_number,
                car=summary.car,
                device_number=summary.device_number,
                shift_details=summary.shift_details,
            )

        return result

    async def _link_brigades(
        self,
        summaries: list[UploadParsedSummaryDTO],
        wells_by_name: dict[str, Well],
    ) -> int:
        """Привязать бригаду к ремонту, покрывающему сутки сводки.

        Данные тянутся тремя батчевыми запросами (ремонты, существующие связки,
        бригады) — на батче в тысячи сводок построчный поиск давал N+1.
        """
        pairs = {
            (wells_by_name[s.well_name].id, s.start_date, s.brigade_number)
            for s in summaries
            if s.brigade_number > 0
        }
        if not pairs:
            return 0

        well_ids = {well_id for well_id, _, _ in pairs}
        dates = [summary_date for _, summary_date, _ in pairs]
        # Repair.well_id загрузчиком не заполняется — держим обратный маппинг
        # abai_well_id → well_id, чтобы разложить ремонты по скважинам.
        well_id_by_abai = {
            well.abai_id: well.id
            for well in wells_by_name.values()
            if well.id in well_ids
        }
        repairs = await self.repair_repository.list_covering_range(
            sorted(well_ids),
            sorted(well_id_by_abai),
            date_from=min(dates),
            date_to=max(dates),
        )
        repairs_by_well: dict[int, list[Repair]] = defaultdict(list)
        for repair in repairs:
            well_id = repair.well_id or well_id_by_abai.get(repair.abai_well_id)
            if well_id is not None:
                repairs_by_well[well_id].append(repair)

        matched: dict[int, int] = {}  # repair_id -> brigade_number
        for well_id, summary_date, brigade_number in sorted(pairs):
            repair = self._pick_repair(repairs_by_well.get(well_id, ()), summary_date)
            if repair is None:
                logger.debug(
                    "No repair covering well_id=%s date=%s — skipping brigade link.",
                    well_id,
                    summary_date,
                )
                continue
            matched.setdefault(repair.id, brigade_number)

        if not matched:
            return 0

        already_linked = {
            link.repair_id
            for link in await self.repair_brigade_repository.list_by_repair_ids(
                list(matched),
            )
        }
        pending = {
            repair_id: brigade_number
            for repair_id, brigade_number in matched.items()
            if repair_id not in already_linked
        }
        if not pending:
            return 0

        brigades_by_name = await self._resolve_brigades(set(pending.values()))

        created = 0
        for repair_id, brigade_number in pending.items():
            brigade = brigades_by_name.get(f"Бригада №{brigade_number}")
            if brigade is None:
                continue
            await self.repair_brigade_repository.create(
                CreateRepairBrigadeDTO(repair_id=repair_id, brigade_id=brigade.id),
            )
            created += 1
        return created

    async def _resolve_brigades(
        self,
        brigade_numbers: set[int],
    ) -> dict[str, UniqueBrigade]:
        names = [f"Бригада №{number}" for number in sorted(brigade_numbers)]
        brigades = await self.unique_brigade_repository.list_by_names(names)
        brigades_by_name = {brigade.name: brigade for brigade in brigades}

        missing = set(names) - brigades_by_name.keys()
        if missing:
            logger.warning(
                "UniqueBrigade rows not found, links skipped: %s",
                sorted(missing),
            )
        return brigades_by_name

    @staticmethod
    def _pick_repair(repairs: list[Repair], target_date: date) -> Repair | None:
        """Самый свежий ремонт скважины, покрывающий эти сутки."""
        covering = [
            repair
            for repair in repairs
            if repair.start_time.date() <= target_date
            and (repair.end_time is None or repair.end_time.date() >= target_date)
        ]
        if not covering:
            return None
        return max(covering, key=lambda repair: repair.start_time)

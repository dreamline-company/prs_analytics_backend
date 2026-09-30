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

import re
from collections import defaultdict
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from apps.org.models.brigade import UniqueBrigade
from apps.org.repositories import UniqueBrigadeRepository
from apps.org.use_cases.get_ngdu_for_well import GetNGDUForWellUseCase
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
from apps.repairs.tasks.fetch_sources.triggers import schedule_transport_fetch
from apps.wells.models.well import Well
from apps.wells.repositories import WellRepository
from core import get_logger
from shared.errors import HttpError

logger = get_logger(__name__)


# «KRK_179V», «SKS_004P»: префикс, 3–4 цифры и одна буква-суффикс.
_SUFFIXED_NAME_RE = re.compile(r"^([A-Z]{3})_(\d{3,4})[A-Za-z]$")
# «DSR_1/1»: доссорские скважины с дробью, однозначный номер без нуля.
_SLASH_NAME_RE = re.compile(r"^([A-Z]{3})_(\d)/(\d+)$")


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
        get_ngdu_for_well: GetNGDUForWellUseCase,
    ) -> None:
        self.session = session
        self.well_repository = well_repository
        self.repair_summary_repository = repair_summary_repository
        self.repair_repository = repair_repository
        self.repair_brigade_repository = repair_brigade_repository
        self.unique_brigade_repository = unique_brigade_repository
        self.get_ngdu_for_well = get_ngdu_for_well

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

        repairs_by_well = await self._load_repairs_by_well(accepted, wells_by_name)
        dtos_by_key = self._build_create_dtos(accepted, wells_by_name, repairs_by_well)
        existing_keys = await self.repair_summary_repository.list_existing_keys(
            dtos_by_key.keys(),
        )
        await self.repair_summary_repository.bulk_upsert(list(dtos_by_key.values()))

        linked_brigades = await self._link_brigades(
            accepted,
            wells_by_name,
            repairs_by_well,
        )

        await self.session.commit()

        await self._schedule_transport_fetch(accepted, wells_by_name)

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
            fallback = await self._resolve_fallback(unknown)
            wells_by_name.update(fallback)
            unknown = unknown - fallback.keys()
        if unknown:
            logger.warning(
                "Unknown wells in parsed summaries (%s): %s",
                len(unknown),
                sorted(unknown)[:20],
            )
        return wells_by_name, unknown

    async def _resolve_fallback(self, names: set[str]) -> dict[str, Well]:
        """Запасное разрешение имён из сводок, которых нет в ``wells_well`` как есть.

        Сводки пишут номер скважины руками, поэтому:
          1. регистр суффикса не совпадает с БД (``SKS_004P`` -> ``SKS_004p``);
          2. буква после номера — пометка, а не часть имени: в БД есть только
             ``KRK_0179``, а в сводке «179В» -> ``KRK_179V``.
        Сначала поиск без учёта регистра, затем — то же имя без суффикса.
        """
        resolved: dict[str, Well] = {}
        ci = await self.well_repository.list_by_names_ci(list(names))
        by_lower = {well.name.lower(): well for well in ci}
        for name in names:
            well = by_lower.get(name.lower())
            if well is not None:
                resolved[name] = well

        stripped: dict[str, str] = {}  # исходное имя -> кандидат в БД
        for name in names - resolved.keys():
            m = _SUFFIXED_NAME_RE.match(name)
            if m:
                stripped[name] = f"{m.group(1)}_{int(m.group(2)):04d}"
                continue
            m = _SLASH_NAME_RE.match(name)
            if m:  # «DSR_1/1» в БД записана как «DSR_01/1»
                stripped[name] = f"{m.group(1)}_0{m.group(2)}/{m.group(3)}"
        if stripped:
            plain = await self.well_repository.list_by_names(
                list(set(stripped.values())),
            )
            plain_by_name = {well.name: well for well in plain}
            for name, candidate in stripped.items():
                well = plain_by_name.get(candidate)
                if well is not None:
                    resolved[name] = well
        if resolved:
            logger.info(
                "Parsed summaries: %s well name(s) resolved by fallback: %s",
                len(resolved),
                {name: well.name for name, well in sorted(resolved.items())[:20]},
            )
        return resolved

    async def _load_repairs_by_well(
        self,
        summaries: list[UploadParsedSummaryDTO],
        wells_by_name: dict[str, Well],
    ) -> dict[int, list[Repair]]:
        """Ремонты скважин батча, пересекающие диапазон дат сводок, по well_id.

        Один батчевый запрос на загрузку: по нему сводке проставляется
        ``repair_id`` (иначе выдача сводок по ремонту и шаг «Сводка» в таймлайне
        пусты) и привязывается бригада. ``Repair.well_id`` загрузчиком не
        заполняется — держим обратный маппинг abai_well_id → well_id.
        """
        wells = {wells_by_name[s.well_name] for s in summaries}
        if not wells:
            return {}
        dates = [s.start_date for s in summaries]
        well_id_by_abai = {well.abai_id: well.id for well in wells}
        repairs = await self.repair_repository.list_covering_range(
            sorted(well.id for well in wells),
            sorted(well_id_by_abai),
            date_from=min(dates),
            date_to=max(dates),
        )
        repairs_by_well: dict[int, list[Repair]] = defaultdict(list)
        for repair in repairs:
            well_id = repair.well_id or well_id_by_abai.get(repair.abai_well_id)
            if well_id is not None:
                repairs_by_well[well_id].append(repair)
        return repairs_by_well

    @classmethod
    def _build_create_dtos(
        cls,
        summaries: list[UploadParsedSummaryDTO],
        wells_by_name: dict[str, Well],
        repairs_by_well: dict[int, list[Repair]],
    ) -> dict[SummaryKey, CreateRepairSummaryDTO]:
        """Схлопнуть батч до одной записи на ключ «скважина × сутки × смена».

        Внутри месячного файла одна и та же смена может встретиться дважды
        (повтор строки, склейка листов) — берём последнюю: она свежее.
        ``repair_id`` — самый свежий ремонт скважины, покрывающий сутки сводки.
        """
        result: dict[SummaryKey, CreateRepairSummaryDTO] = {}
        for summary in summaries:
            well_id = wells_by_name[summary.well_name].id
            second_well = (
                wells_by_name.get(summary.second_well_name)
                if summary.second_well_name
                else None
            )
            repair = cls._pick_repair(
                repairs_by_well.get(well_id, []),
                summary.start_date,
            )

            key: SummaryKey = (well_id, summary.start_date, summary.shift_type_number)
            result[key] = CreateRepairSummaryDTO(
                repair_id=repair.id if repair is not None else None,
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
        repairs_by_well: dict[int, list[Repair]],
    ) -> int:
        """Привязать бригаду к ремонту, покрывающему сутки сводки.

        Бригада ищется в НГДУ скважины: «Бригада №3» есть в каждом НГДУ.
        Данные тянутся батчевыми запросами (существующие связки, бригады;
        ремонты уже загружены) — на батче в тысячи сводок построчный поиск
        давал N+1.
        """
        pairs = {
            (wells_by_name[s.well_name].id, s.start_date, s.brigade_number)
            for s in summaries
            if s.brigade_number > 0
        }
        if not pairs:
            return 0

        wells_by_id = {well.id: well for well in wells_by_name.values()}
        # repair_id -> (brigade_number, well_id)
        matched: dict[int, tuple[int, int]] = {}
        for well_id, summary_date, brigade_number in sorted(pairs):
            repair = self._pick_repair(repairs_by_well.get(well_id, ()), summary_date)
            if repair is None:
                logger.debug(
                    "No repair covering well_id=%s date=%s — skipping brigade link.",
                    well_id,
                    summary_date,
                )
                continue
            matched.setdefault(repair.id, (brigade_number, well_id))

        if not matched:
            return 0

        already_linked = {
            link.repair_id
            for link in await self.repair_brigade_repository.list_by_repair_ids(
                list(matched),
            )
        }
        pending = {
            repair_id: value
            for repair_id, value in matched.items()
            if repair_id not in already_linked
        }
        if not pending:
            return 0

        ngdu_by_well: dict[int, int | None] = {}
        for _, well_id in pending.values():
            if well_id not in ngdu_by_well:
                ngdu = await self.get_ngdu_for_well.execute(
                    wells_by_id[well_id].abai_id,
                )
                ngdu_by_well[well_id] = ngdu.id if ngdu is not None else None
        brigades = await self._resolve_brigades(
            {number for number, _ in pending.values()},
        )

        created = 0
        for repair_id, (brigade_number, well_id) in pending.items():
            brigade = brigades.get(
                (f"Бригада №{brigade_number}", ngdu_by_well[well_id]),
            )
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
    ) -> dict[tuple[str, int], UniqueBrigade]:
        """Бригады с этими номерами во всех НГДУ, по ключу (имя, НГДУ)."""
        names = [f"Бригада №{number}" for number in sorted(brigade_numbers)]
        brigades = await self.unique_brigade_repository.list_by_names(names)
        by_key = {(brigade.name, brigade.ngdu_id): brigade for brigade in brigades}

        missing = set(names) - {name for name, _ in by_key}
        if missing:
            logger.warning(
                "UniqueBrigade rows not found, links skipped: %s",
                sorted(missing),
            )
        return by_key

    async def _schedule_transport_fetch(
        self,
        summaries: list[UploadParsedSummaryDTO],
        wells_by_name: dict[str, Well],
    ) -> None:
        """Поставить добытчик путёвок УТО на ремонты, покрытые новыми сводками.

        Сводка — единственный источник пар «машина × дата» для УТО, поэтому
        первый запрос в УТО правильно делать сразу после загрузки, а не ждать
        крона. Сбой здесь не должен ронять ответ API: данные уже закоммичены.
        """
        try:
            repair_ids = await self._covering_repair_ids(summaries, wells_by_name)
            if repair_ids:
                schedule_transport_fetch(repair_ids)
        except Exception:
            logger.exception("Transport fetch trigger failed; cron will catch up.")

    async def _covering_repair_ids(
        self,
        summaries: list[UploadParsedSummaryDTO],
        wells_by_name: dict[str, Well],
    ) -> list[int]:
        pairs = {(wells_by_name[s.well_name].id, s.start_date) for s in summaries}
        if not pairs:
            return []
        well_ids = {well_id for well_id, _ in pairs}
        dates = [summary_date for _, summary_date in pairs]
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
        matched: set[int] = set()
        for well_id, summary_date in pairs:
            repair = self._pick_repair(repairs_by_well.get(well_id, ()), summary_date)
            if repair is not None:
                matched.add(repair.id)
        return sorted(matched)

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

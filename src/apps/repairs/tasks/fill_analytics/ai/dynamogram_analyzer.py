"""ИИ-разбор одной динамограммы — общий для обоих писателей.

Писателей двое: аналитика ремонта (пары до/после) и проход по фонду
(``apps.wells.tasks.analyze_dynamograms``). Оба работают через этот класс,
поэтому «разбирать ли», подготовка файла, параметры ГДИС и запись в
``repairs_dynamogram_ai_result`` — в одном месте и не расходятся.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from botocore.exceptions import ClientError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.files.repositories.file import FileRepository
from apps.repairs.dto.internal.repositories.ai_results import (
    CreateRepairDynamogramAIResultDTO,
)
from apps.repairs.models.analytics import (
    AI_STATUS_COMPLETED,
    AI_STATUS_FAILED,
    RepairDynamogramAIResult,
)
from apps.repairs.repositories.ai_results import (
    RepairDynamogramAIResultRepository,
    version_number,
)
from apps.wells.models.dynamogram import Dynamogram
from apps.wells.repositories.gdis import (
    GdisCurrentRepository,
    GdisCurrentValueRepository,
    GdisMetricRepository,
)
from apps.wells.services.gdis import DynamogramGdisService
from core import get_logger
from core.settings import get_settings
from shared.database.s3.storage import AiobotoFileStorage, FileNotExistError

from .agent_factory import build_dynamogram_agent
from .base import AIProcessingResult, TokenUsage
from .dynamogram_image import UnsupportedDynamogramFileError, prepare_image
from .dynamogram_processor import DynamogramAIProcessor, DynamogramProcessingInput

logger = get_logger(__name__)

# Упавший разбор (сбой LLM) повторяется не чаще: аналитика ремонта ходит
# каждый час, проход по фонду — каждые полчаса.
FAILED_RETRY_AFTER = timedelta(hours=6)
# Файл, который модель не прочитает (HTML вместо картинки, пустой, нет в
# S3), не повторяется до смены версии промпта: повтор упадёт так же. Когда
# битые файлы перекачают, их строки надо удалить — разбор пойдёт заново.
INPUT_REJECTED_PREFIX = "unsupported input: "
# Коды ClientError, означающие «объекта нет»; остальные (403, 5xx) — сбой S3,
# а не свойство файла: такая строка не пишется и разбор повторится.
_MISSING_KEY_CODES = frozenset({"404", "NoSuchKey", "NotFound"})


def needs_analysis(
    row: RepairDynamogramAIResult | None,
    *,
    prompt_version: str,
    now: datetime,
) -> bool:
    if row is None:
        return True
    row_version = version_number(row.prompt_version)
    current = version_number(prompt_version)
    if row_version != current:
        # Более новую версию (её записал более новый код) не трогаем.
        return row_version < current
    if row.status == AI_STATUS_COMPLETED:
        return False
    if row.status != AI_STATUS_FAILED:
        return True
    if (row.error or "").startswith(INPUT_REJECTED_PREFIX):
        return False
    return row.processed_at is None or now - row.processed_at >= FAILED_RETRY_AFTER


@dataclass(slots=True)
class DynamogramAnalyzer:
    processor: DynamogramAIProcessor
    ai_repo: RepairDynamogramAIResultRepository
    file_repo: FileRepository
    storage: AiobotoFileStorage
    gdis_service: DynamogramGdisService

    async def analyze(
        self,
        dynamogram: Dynamogram,
        *,
        abai_well_id: int | None,
        wait_for_lock: bool = True,
    ) -> tuple[RepairDynamogramAIResult | None, TokenUsage]:
        """Разобрать, если разбор не актуален; вернуть строку и расход токенов.

        Сначала — транзакционный замок динамограммы: второй писатель не платит
        за тот же вызов LLM и видит уже записанный разбор. Аналитика ремонта
        ждёт замок (транзакция прохода по фонду короткая); проход по фонду
        (``wait_for_lock=False``) занятую динамограмму пропускает — ``None``:
        аналитика ремонта держит транзакцию минутами.
        """
        if not await self.ai_repo.lock_dynamogram(
            dynamogram.id,
            wait=wait_for_lock,
        ):
            return None, TokenUsage()
        existing = await self.ai_repo.get_by_dynamogram_id(dynamogram.id)
        now = _local_now()
        if not needs_analysis(
            existing,
            prompt_version=self.processor.prompt_version,
            now=now,
        ):
            return existing, TokenUsage()

        result = await self._run(dynamogram, abai_well_id=abai_well_id, now=now)
        row = await self.ai_repo.upsert(
            CreateRepairDynamogramAIResultDTO(
                dynamogram_id=dynamogram.id,
                status=result.status,
                model_name=result.model_name,
                prompt_version=result.prompt_version,
                result=result.result,
                error=result.error,
                processed_at=result.processed_at,
            ),
        )
        return row, result.usage

    async def _run(
        self,
        dynamogram: Dynamogram,
        *,
        abai_well_id: int | None,
        now: datetime,
    ) -> AIProcessingResult:
        file_row = await self.file_repo.get_by_id(dynamogram.file_id)
        if file_row is None:
            return self._rejected("no file row", now=now)
        try:
            payload = (await self.storage.download_file(file_row.file)).getvalue()
        except FileNotExistError as exc:
            if not _is_missing_key(exc):
                raise
            return self._rejected("missing in S3", now=now)
        try:
            image, mime = prepare_image(payload)
        except UnsupportedDynamogramFileError as exc:
            kind = exc.kind
        except Exception:
            # Файл, на котором падает разбор, — свойство файла: повтор упадёт
            # так же, а исключение откатило бы весь ремонт в аналитике.
            logger.exception("Dynamogram id=%s: file not prepared.", dynamogram.id)
            kind = "unreadable"
        else:
            kind = None
        if kind is not None:
            logger.info(
                "Dynamogram id=%s skipped: file %s is %s.",
                dynamogram.id,
                file_row.file,
                kind,
            )
            return self._rejected(kind, now=now)

        gdis = (
            await self.gdis_service.get_for_dynamogram(
                abai_well_id,
                dynamogram.snapshot_time.date(),
            )
            if abai_well_id is not None
            else None
        )
        return await self.processor.process(
            DynamogramProcessingInput(
                dynamogram=dynamogram,
                s3_key=file_row.file,
                image_bytes=image,
                image_mime=mime,
                gdis=gdis,
            ),
        )

    def _rejected(self, reason: str, *, now: datetime) -> AIProcessingResult:
        return AIProcessingResult(
            status=AI_STATUS_FAILED,
            result=None,
            error=f"{INPUT_REJECTED_PREFIX}{reason}",
            model_name=None,
            prompt_version=self.processor.prompt_version,
            processed_at=now,
        )


def _is_missing_key(exc: FileNotExistError) -> bool:
    cause = exc.__cause__
    if not isinstance(cause, ClientError):
        return False
    return str(cause.response.get("Error", {}).get("Code")) in _MISSING_KEY_CODES


def build_dynamogram_processor() -> DynamogramAIProcessor:
    return DynamogramAIProcessor(
        build_dynamogram_agent(),
        model_name=get_settings().LLM_MODEL_NAME,
    )


def build_dynamogram_analyzer(
    session: AsyncSession,
    *,
    storage: AiobotoFileStorage,
    processor: DynamogramAIProcessor,
) -> DynamogramAnalyzer:
    return DynamogramAnalyzer(
        processor=processor,
        ai_repo=RepairDynamogramAIResultRepository(session),
        file_repo=FileRepository(session),
        storage=storage,
        gdis_service=DynamogramGdisService(
            gdis_metric_repository=GdisMetricRepository(session),
            gdis_current_repository=GdisCurrentRepository(session),
            gdis_current_value_repository=GdisCurrentValueRepository(session),
        ),
    )


def _local_now() -> datetime:
    return datetime.now(get_settings().ZONE_INFO).replace(tzinfo=None)

"""Все динамограммы скважины со ссылками на файлы и ИИ-разборами."""

from datetime import UTC, datetime, timedelta

from apps.files.repositories.file import FileRepository
from apps.repairs.repositories.ai_results import RepairDynamogramAIResultRepository
from apps.wells.dto.internal.dynamogram import WellDynamogramAIDTO, WellDynamogramDTO
from apps.wells.dto.queries.well import ListWellDynamogramsQuery
from apps.wells.repositories.dynamogram import DynamogramRepository
from apps.wells.repositories.well import WellRepository
from apps.wells.use_cases.get_well_card import WellNotFoundError
from shared.database.s3.storage import AiobotoFileStorage


class ListDynamogramsByWellIdUseCase:
    def __init__(
        self,
        *,
        well_repository: WellRepository,
        dynamogram_repository: DynamogramRepository,
        file_repository: FileRepository,
        dynamogram_ai_repository: RepairDynamogramAIResultRepository,
        storage: AiobotoFileStorage,
    ) -> None:
        self.well_repository = well_repository
        self.dynamogram_repository = dynamogram_repository
        self.file_repository = file_repository
        self.dynamogram_ai_repository = dynamogram_ai_repository
        self.storage = storage

    async def execute(
        self,
        query: ListWellDynamogramsQuery,
    ) -> list[WellDynamogramDTO]:
        # Проверка существования скважины отделяет опечатку в well_id от
        # честного «динамограмм нет»: и то, и другое иначе вернуло бы [].
        well = await self.well_repository.get_by_id(id_=query.well_id)
        if well is None:
            raise WellNotFoundError(details={"well_id": query.well_id})

        dynamograms = await self.dynamogram_repository.list_by_well_id(well.id)
        if not dynamograms:
            return []

        files = await self.file_repository.list_by_ids(
            [dynamogram.file_id for dynamogram in dynamograms],
        )
        keys_by_file_id = {file.id: file.file for file in files}
        # Подписываются все ключи разом — один клиент S3 на выдачу, а не на
        # динамограмму.
        urls = await self.storage.generate_presigned_urls(
            keys_by_file_id.values(),
            expires_in=query.expires_in,
        )
        expires_at = datetime.now(UTC) + timedelta(seconds=query.expires_in)
        ai_by_dynamogram_id = {
            row.dynamogram_id: WellDynamogramAIDTO.from_result(
                status=row.status,
                prompt_version=row.prompt_version,
                model_name=row.model_name,
                processed_at=row.processed_at,
                error=row.error,
                result=row.result,
            )
            for row in await self.dynamogram_ai_repository.list_by_dynamogram_ids(
                [dynamogram.id for dynamogram in dynamograms],
            )
        }

        return [
            WellDynamogramDTO(
                id=dynamogram.id,
                well_id=dynamogram.well_id,
                snapshot_time=dynamogram.snapshot_time,
                file_id=dynamogram.file_id,
                file_key=keys_by_file_id.get(dynamogram.file_id),
                download_url=urls.get(keys_by_file_id.get(dynamogram.file_id, "")),
                expires_at=(
                    expires_at if dynamogram.file_id in keys_by_file_id else None
                ),
                ai=ai_by_dynamogram_id.get(dynamogram.id),
            )
            for dynamogram in dynamograms
        ]

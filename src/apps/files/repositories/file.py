from collections.abc import Sequence

from apps.files.dto.internal.repositories.file import CreateFileDTO, UpdateFileDTO
from apps.files.models.file import File
from shared.repository.sqlalchemy import AsyncAlchemyRepository, QuerySpec


class FileRepository(AsyncAlchemyRepository[CreateFileDTO, UpdateFileDTO, File]):
    model = File

    async def get_by_id(self, file_id: int) -> File | None:
        return await self.get_one(
            QuerySpec(filters=(File.id == file_id,)),
        )

    async def get_by_path(self, path: str) -> File | None:
        return await self.get_one(
            QuerySpec(filters=(File.file == path,)),
        )

    async def list_by_ids(self, file_ids: Sequence[int]) -> Sequence[File]:
        if not file_ids:
            return ()

        return await self.get_list(
            QuerySpec(
                filters=(File.id.in_(file_ids),),
                order_by=(File.id,),
            ),
        )

    async def delete_by_id(self, file_id: int) -> None:
        await self.delete(filters=(File.id == file_id,))

    async def update_by_id(self, file_id: int, data: UpdateFileDTO) -> File:
        return await self.update(data=data, filters=(File.id == file_id,))

from collections.abc import Sequence

from sqlalchemy import delete, update

from shared.database.sql.models import AppBaseModel
from shared.dto.repositories import RepositoryDTO
from shared.repository.sqlalchemy import AsyncAlchemyRepository, QuerySpec


class AbaiMirrorRepository[DTOT: RepositoryDTO, ModelT: AppBaseModel](
    AsyncAlchemyRepository[DTOT, DTOT, ModelT],
):
    """Зеркало одной таблицы ABAI: читается целиком и сверяется по ``abai_id``.

    Один DTO и для вставки, и для обновления: сверка всегда пишет строку
    целиком. Модели нужен уникальный ``abai_id`` (``AbaiIdMixin``).

    Правка и удаление идут без синхронизации сессии: перед ними сверка держит в
    identity map всё зеркало (``list_all``), и ORM-синхронизация перебирала бы
    его на каждый UPDATE — массовая правка в ABAI (десятки тысяч строк) не
    уложилась бы в лимит времени таски. Загруженные объекты после записи
    устаревают, сверка их больше не читает.
    """

    # Размер IN-списка при удалении исчезнувших в источнике строк.
    DELETE_CHUNK_SIZE = 1000

    async def list_all(self) -> Sequence[ModelT]:
        return await self.get_list(QuerySpec(order_by=(self.model.abai_id,)))

    async def update_by_abai_id(self, data: DTOT) -> None:
        await self.session.execute(
            update(self.model)
            .where(self.model.abai_id == data.abai_id)
            .values(**data.model_dump())
            .execution_options(synchronize_session=False),
        )

    async def delete_by_abai_ids(self, abai_ids: Sequence[int]) -> None:
        for start in range(0, len(abai_ids), self.DELETE_CHUNK_SIZE):
            chunk = abai_ids[start : start + self.DELETE_CHUNK_SIZE]
            await self.session.execute(
                delete(self.model)
                .where(self.model.abai_id.in_(chunk))
                .execution_options(synchronize_session=False),
            )

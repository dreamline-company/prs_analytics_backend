from collections.abc import Sequence

from apps.org.dto.internal.ngdu import NGDUShortDTO
from apps.org.repositories.org import OrgRepository
from shared.constants.ngdu import AbaiNGDUIDsEnum

# В списке НГДУ отдаём только подключённые к системе подразделения;
# остальные из AbaiNGDUIDsEnum намеренно скрыты.
LISTED_NGDU_ABAI_IDS: tuple[AbaiNGDUIDsEnum, ...] = (
    AbaiNGDUIDsEnum.KMG,
    AbaiNGDUIDsEnum.ZHMG,
)
# Список НГДУ для экранов ПРС (list_ngdus_prs).
PRS_NGDU_ABAI_IDS: tuple[AbaiNGDUIDsEnum, ...] = (
    AbaiNGDUIDsEnum.KMG,
    AbaiNGDUIDsEnum.ZHlMG,
)


class ListNGDUsUseCase:
    def __init__(
        self,
        org_repository: OrgRepository,
        abai_ids: Sequence[AbaiNGDUIDsEnum] = LISTED_NGDU_ABAI_IDS,
    ) -> None:
        self.org_repository = org_repository
        self.abai_ids = abai_ids

    async def execute(self) -> list[NGDUShortDTO]:
        orgs = await self.org_repository.list_by_abai_ids(
            [item.value for item in self.abai_ids],
        )
        return [NGDUShortDTO(id=org.id, name=org.name_ru) for org in orgs]

from collections.abc import Sequence

from apps.detectors.dto.internal.repositories.verification import (
    CreateVerificationDTO,
    CreateVerificationHistoryDTO,
    UpdateVerificationDTO,
    UpdateVerificationHistoryDTO,
)
from apps.detectors.models.verification import (
    DetectorVerification,
    DetectorVerificationHistory,
)
from shared.repository.sqlalchemy import AsyncAlchemyRepository, QuerySpec


class DetectorVerificationRepository(
    AsyncAlchemyRepository[
        CreateVerificationDTO,
        UpdateVerificationDTO,
        DetectorVerification,
    ],
):
    model = DetectorVerification

    async def get_map_by_incident_ids(
        self,
        incident_ids: Sequence[int],
    ) -> dict[int, DetectorVerification]:
        """Отметки по списку эпизодов: incident_id -> строка."""
        if not incident_ids:
            return {}
        rows = await self.get_list(
            QuerySpec(filters=(DetectorVerification.incident_id.in_(incident_ids),)),
        )
        return {row.incident_id: row for row in rows}


class DetectorVerificationHistoryRepository(
    AsyncAlchemyRepository[
        CreateVerificationHistoryDTO,
        UpdateVerificationHistoryDTO,
        DetectorVerificationHistory,
    ],
):
    model = DetectorVerificationHistory

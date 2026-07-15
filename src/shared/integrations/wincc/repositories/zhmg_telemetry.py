from shared.integrations.wincc.models import ZHMGWinccTelemetry
from shared.integrations.wincc.repositories.ngdu_telemetry import (
    NGDUWinccTelemetryRepository,
)


class ZHMGWinccTelemetryRepository(
    NGDUWinccTelemetryRepository[ZHMGWinccTelemetry],
):
    model = ZHMGWinccTelemetry

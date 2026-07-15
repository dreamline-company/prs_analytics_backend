from shared.integrations.wincc.models import ZHYLMGWinccTelemetry
from shared.integrations.wincc.repositories.ngdu_telemetry import (
    NGDUWinccTelemetryRepository,
)


class ZHYLMGWinccTelemetryRepository(
    NGDUWinccTelemetryRepository[ZHYLMGWinccTelemetry],
):
    model = ZHYLMGWinccTelemetry

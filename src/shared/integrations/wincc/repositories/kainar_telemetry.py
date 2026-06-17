from shared.integrations.wincc.models import KainarWinccTelemetry
from shared.integrations.wincc.repositories.ngdu_telemetry import (
    NGDUWinccTelemetryRepository,
)


class KainarWinccTelemetryRepository(
    NGDUWinccTelemetryRepository[KainarWinccTelemetry],
):
    model = KainarWinccTelemetry

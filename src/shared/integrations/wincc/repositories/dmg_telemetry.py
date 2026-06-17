from shared.integrations.wincc.models import DMGWinccTelemetry
from shared.integrations.wincc.repositories.ngdu_telemetry import (
    NGDUWinccTelemetryRepository,
)


class DMGWinccTelemetryRepository(
    NGDUWinccTelemetryRepository[DMGWinccTelemetry],
):
    model = DMGWinccTelemetry

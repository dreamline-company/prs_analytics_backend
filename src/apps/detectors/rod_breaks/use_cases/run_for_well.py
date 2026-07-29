from datetime import datetime

from apps.detectors.rod_breaks.detector import DetectionResult, RodBreakDetector


class RunRodBreakForWell:
    """Прогнать правило R2 по одной скважине на дату as_of."""

    def __init__(self, detector: RodBreakDetector) -> None:
        self.detector = detector

    async def execute(self, well_id: int, as_of: datetime) -> DetectionResult:
        return await self.detector.run_for_well(well_id, as_of)

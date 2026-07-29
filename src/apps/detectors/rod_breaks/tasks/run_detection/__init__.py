import asyncio

from apps.celery_app import celery_app
from apps.detectors.rod_breaks.tasks.run_detection.run_detection import (
    RunRodBreakDetection,
)


@celery_app.task(name="detectors.rod_breaks.run")
def run_rod_break_detection() -> None:
    asyncio.run(RunRodBreakDetection().run())

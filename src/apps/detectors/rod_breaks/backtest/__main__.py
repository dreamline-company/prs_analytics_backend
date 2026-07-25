"""CLI ``python -m apps.detectors.rod_breaks.backtest``.

Флаги: ``--well-ids 1,2,3`` (по умолчанию все с fc_data),
``--output-dir path`` (по умолчанию ``reports/rod_breaks_backtest/<ts>``).

Прогон сериальный (одна скважина за раз): полный load per well + сборка
HTML/SVG сразу, серия отбрасывается — держим память ограниченной.
"""

# ruff: noqa: T201

import argparse
import asyncio
import time
from datetime import UTC, datetime
from pathlib import Path

from apps.detectors.rod_breaks.backtest import report, runner
from apps.detectors.rod_breaks.services.telemetry_source import (
    RodBreakTelemetrySource,
)
from apps.models_registry import *  # noqa: F403
from core import get_logger
from shared.database.sql.setup import session_makers

logger = get_logger(__name__)

_DEFAULT_OUTPUT_ROOT = Path("reports/rod_breaks_backtest")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="R2 rod-break rule backtest")
    parser.add_argument(
        "--well-ids",
        type=str,
        default=None,
        help="Список well_id через запятую (по умолчанию все с fc_data)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Куда писать отчёт (default reports/rod_breaks_backtest/<ts>)",
    )
    return parser.parse_args()


async def run(
    well_ids_filter: list[int] | None,
    output_dir: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240 — CLI, one-shot
    generated_at = datetime.now(UTC).replace(tzinfo=None)

    async with session_makers["app"]() as session:
        targets = await runner.collect_targets(session)
        if well_ids_filter is not None:
            wanted = set(well_ids_filter)
            targets = [t for t in targets if t.well_id in wanted]
        logger.info("Backtest targets: %s wells", len(targets))

        rod_break, other = await runner.load_repairs(
            session,
            [t.well_id for t in targets],
        )
        logger.info(
            "Repairs loaded: %s wells with rod-break, %s wells with other",
            sum(1 for v in rod_break.values() if v),
            sum(1 for v in other.values() if v),
        )

        source = RodBreakTelemetrySource(session)
        results: list[runner.WellResult] = []
        started = time.monotonic()
        for i, target in enumerate(targets, 1):
            result = await runner.analyze_well(
                source,
                target,
                rod_break.get(target.well_id, []),
                other.get(target.well_id, []),
            )
            report.render_well_page(
                result,
                output_dir / f"well_{target.well_id}.html",
            )
            results.append(result)
            elapsed = time.monotonic() - started
            logger.info(
                "[%s/%s] well %s: TP=%s FN=%s FP=%s+%s+%s%s (elapsed %.1fs)",
                i,
                len(targets),
                target.well_id,
                result.tp,
                result.fn,
                result.fp_transient,
                result.fp_other_repair,
                result.fp_no_repair,
                f" skipped={result.skipped_reason}" if result.skipped_reason else "",
                elapsed,
            )

    report.render_index(results, output_dir, generated_at)
    logger.info("Wrote index.html to %s", output_dir)
    print(f"Report ready: {output_dir / 'index.html'}")


async def main() -> None:
    args = _parse_args()
    well_ids_filter = (
        [int(x) for x in args.well_ids.split(",")] if args.well_ids else None
    )
    output_dir = args.output_dir or (
        _DEFAULT_OUTPUT_ROOT / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    )
    await run(well_ids_filter, output_dir)


if __name__ == "__main__":
    asyncio.run(main())

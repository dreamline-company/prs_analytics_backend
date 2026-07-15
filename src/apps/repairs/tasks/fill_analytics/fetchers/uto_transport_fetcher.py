"""Fetches special-transport waybills for a repair from UTO.

Flow per repair:
  1. Read all ``RepairSummary`` rows for the repair (``repairs_repair_reports``).
     Each row has a ``car`` (free-form vehicle number string) and a ``date``.
  2. Deduplicate the ``(car, date)`` pairs across summaries — same car on the
     same day means the same UTO query.
  3. For each unique pair, call ``UtoWaybillClient.search_all_waybills`` (sync
     RPC, so wrapped in ``asyncio.to_thread``) — returns 0..N waybills.
  4. Each waybill (``WaybillDbRowDto``) becomes one row in ``repairs_transport``:
     * if a row with the same ``(repair_id, request_id)`` already exists — update
       fields (fresh status/actual_date/etc);
     * otherwise create a new row and link it to the repair.

The UTO API returns ISO-8601 date strings and mixed-type numerics — we parse
them into ``datetime`` and coerce numerics before handing to the DTO.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from apps.repairs.dto.internal.repositories.transport import (
    CreateRepairTransportDTO,
    UpdateRepairTransportDTO,
)
from apps.repairs.models.repair import Repair
from apps.repairs.models.transport import RepairTransport
from apps.repairs.repositories.reports import RepairSummaryRepository
from apps.repairs.repositories.transport import RepairTransportRepository
from core import get_logger
from shared.integrations.uto.api.client import (
    UtoClientError,
    UtoWaybillClient,
    WaybillDbRowDto,
)

logger = get_logger(__name__)


class UtoTransportFetcher:
    def __init__(
        self,
        client: UtoWaybillClient,
        summary_repo: RepairSummaryRepository,
        transport_repo: RepairTransportRepository,
    ) -> None:
        self._client = client
        self._summary_repo = summary_repo
        self._transport_repo = transport_repo

    async def fetch_for_repair(self, repair: Repair) -> list[RepairTransport]:
        summaries = await self._summary_repo.list_by_repair_id(repair.id)
        if not summaries:
            logger.info(
                "Transport skipped for repair id=%s — no RepairSummary rows.",
                repair.id,
            )
            return []

        pairs = self._unique_car_date_pairs(summaries)
        logger.info(
            "Repair id=%s: unique (car, date) pairs from summaries: %s",
            repair.id,
            len(pairs),
        )

        results: list[RepairTransport] = []
        for car, day in pairs:
            try:
                waybills = await asyncio.to_thread(
                    self._client.search_all_waybills,
                    car_number=car,
                    target_date=day,
                )
            except UtoClientError:
                logger.exception(
                    "UTO search failed for repair id=%s car=%r day=%s; skipping pair.",
                    repair.id,
                    car,
                    day,
                )
                continue

            if not waybills:
                continue

            logger.info(
                "Repair id=%s: UTO returned %s waybill(s) for car=%r day=%s.",
                repair.id,
                len(waybills),
                car,
                day,
            )

            for waybill in waybills:
                row = await self._upsert_waybill(repair, waybill)
                if row is not None:
                    results.append(row)

        return results

    @staticmethod
    def _unique_car_date_pairs(
        summaries: Sequence,
    ) -> list[tuple[str, date]]:
        seen: set[tuple[str, date]] = set()
        pairs: list[tuple[str, date]] = []
        for s in summaries:
            car = (s.car or "").strip()
            if not car or s.date is None:
                continue
            key = (car, s.date)
            if key in seen:
                continue
            seen.add(key)
            pairs.append(key)
        return pairs

    async def _upsert_waybill(
        self,
        repair: Repair,
        waybill: WaybillDbRowDto,
    ) -> RepairTransport | None:
        request_id = _coerce_int(waybill.request_id)
        if request_id is None:
            logger.warning(
                "Repair id=%s: waybill without usable request_id (%r); skipping.",
                repair.id,
                waybill.request_id,
            )
            return None

        payload = _waybill_to_payload(waybill)

        existing = await self._transport_repo.get_by_repair_id_and_request_id(
            repair_id=repair.id,
            request_id=request_id,
        )
        if existing is not None:
            update_dto = UpdateRepairTransportDTO(**payload)
            return await self._transport_repo.update_by_id(
                repair_transport_id=existing.id,
                data=update_dto,
            )

        create_dto = CreateRepairTransportDTO(
            repair_id=repair.id,
            request_id=request_id,
            **payload,
        )
        return await self._transport_repo.create(create_dto)


def _waybill_to_payload(waybill: WaybillDbRowDto) -> dict[str, Any]:
    """Flatten UTO DTO into the flat model shape.

    Note: ``request_id`` is intentionally excluded — the caller passes it
    explicitly (needed for both create and lookup).
    """

    vehicle_class = waybill.vehicle_class
    return {
        "operation_code": waybill.operation_code,
        "operation_number": waybill.operation_number,
        "status_id": _coerce_int(waybill.status_id),
        "status_name": waybill.status_name,
        "closure_status": waybill.closure_status,
        "department": waybill.department,
        "position": waybill.position,
        "operation_created_at": _parse_iso_datetime(waybill.created_at),
        "planned_start_at": _parse_iso_datetime(waybill.planned_start_at),
        "planned_end_at": _parse_iso_datetime(waybill.planned_end_at),
        "actual_date": _parse_iso_datetime(waybill.actual_date),
        "engine_hours": _coerce_float(waybill.engine_hours),
        "mileage": _coerce_float(waybill.mileage),
        "transport_equipment_number": _coerce_int(waybill.transport_equipment_number),
        "company": waybill.company,
        "division": waybill.division,
        "bpl": waybill.bpl,
        "well_number": waybill.well_number,
        "work_type": waybill.work_type,
        "vehicle_number": waybill.vehicle_number,
        "vehicle_class_code": str(vehicle_class.code)
        if vehicle_class and vehicle_class.code is not None
        else None,
        "vehicle_class_name": vehicle_class.name if vehicle_class else None,
    }


def _coerce_int(value: Any) -> int | None:  # noqa: ANN401
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_float(value: Any) -> float | None:  # noqa: ANN401
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None

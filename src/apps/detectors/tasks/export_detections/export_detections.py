"""Выгрузка всех детекций в CSV: эпизоды R2/R9/R10 и находки R10.

Строка — эпизод (``record_type=incident``) или находка R10
(``record_type=r10_finding``), к каждой — данные скважины (НГДУ,
месторождение, способ эксплуатации, статус ABAI) и станции СДМО, улики
правила разложены по колонкам, целиком — в ``payload_json``. CSV через «;»,
UTF-8 с BOM — Excel открывает без настроек. Только чтение.

    python -m apps.detectors.tasks.export_detections.export_detections \\
        --out /tmp/detections_all.csv

На сервере — ``make export-detections`` из корня репозитория.
"""

import argparse
import asyncio
import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.models_registry import *  # noqa: F403
from apps.org.repositories.org import OrgRepository
from apps.org.services import well_name_prefix
from apps.org.use_cases.get_ngdu_for_well import GetNGDUForWellUseCase
from apps.wells.repositories.well_expl import WellExplRepository
from apps.wells.repositories.well_org import WellOrgRepository
from core import get_logger
from shared.database.sql.setup import session_makers

logger = get_logger(__name__)


def _ts(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""


def _get(payload: Any, *path: str) -> Any:  # noqa: ANN401
    for key in path:
        if not isinstance(payload, dict):
            return ""
        payload = payload.get(key)
    if payload is None:
        return ""
    return ",".join(map(str, payload)) if isinstance(payload, list) else payload


async def _well_info(session: AsyncSession, well_ids: set[int]) -> dict[int, dict]:
    wells = (
        await session.execute(
            text(
                "select id, name, abai_id, is_deleted from wells_well "
                "where id = any(:ids)",
            ),
            {"ids": list(well_ids)},
        )
    ).all()
    abai_ids = [w.abai_id for w in wells]
    fields: dict[str, dict[int, str]] = {}
    for row in (
        await session.execute(text("select prefix, name, ngdu_id from oil_fields"))
    ).all():
        fields.setdefault(row.prefix, {})[row.ngdu_id] = row.name
    expl = await WellExplRepository(session).get_latest_expl_name_by_abai_well_ids(
        abai_ids,
    )
    # Статусы ABAI хранятся в UTC — в выгрузку местным временем (+5).
    statuses = {
        row.abai_well_id: row
        for row in (
            await session.execute(
                text(
                    "select distinct on (ws.abai_well_id) ws.abai_well_id, "
                    "st.name_ru status, rs.name_ru reason, "
                    "ws.dbeg + interval '5 hours' since "
                    "from wells_well_status ws "
                    "left join wells_well_status_type st on st.abai_id = ws.status "
                    "left join wells_well_status_reason rs on rs.abai_id = ws.reason "
                    "where ws.abai_well_id = any(:ids) "
                    "order by ws.abai_well_id, ws.dbeg desc",
                ),
                {"ids": abai_ids},
            )
        ).all()
    }
    get_ngdu = GetNGDUForWellUseCase(
        well_org_repository=WellOrgRepository(session),
        org_repository=OrgRepository(session),
    )
    info = {}
    for well in wells:
        ngdu = await get_ngdu.execute(well.abai_id)
        prefix = well_name_prefix(well.name) or ""
        status = statuses.get(well.abai_id)
        info[well.id] = {
            "well_id": well.id,
            "well_name": well.name,
            "well_abai_id": well.abai_id,
            "well_is_deleted": well.is_deleted,
            "ngdu_id": ngdu.id if ngdu else "",
            "ngdu_abai_id": ngdu.abai_id if ngdu else "",
            "ngdu_name": ngdu.name_ru if ngdu else "",
            "oil_field_prefix": prefix,
            "oil_field_name": fields.get(prefix, {}).get(ngdu.id if ngdu else None, ""),
            "expl_method": expl.get(well.abai_id, "") or "",
            "abai_status_now": status.status if status else "",
            "abai_status_reason": status.reason if status else "",
            "abai_status_since": _ts(status.since) if status else "",
        }
    return info


def _incident_row(row: Any, info: dict[int, dict]) -> dict:  # noqa: ANN401
    p = row["payload"] or {}
    end = row["normalized_at"]
    return {
        "record_type": "incident",
        "incident_id": row["id"],
        "detector_code": row["detector_code"],
        "detector_name": row["detector_name"],
        "detector_source": row["detector_source"],
        "reason_code": row["reason_code"],
        "level": row["level"],
        "status": row["status"],
        "opened_at": _ts(row["opened_at"]),
        "detected_at": _ts(row["detected_at"]),
        "last_seen_at": _ts(row["last_seen_at"]),
        "escalated_at": _ts(row["escalated_at"]),
        "normalized_at": _ts(end),
        "close_reason": row["close_reason"] or "",
        "duration_days": round(
            ((end or row["last_seen_at"]) - row["opened_at"]).total_seconds() / 86400,
            2,
        ),
        "config_version": row["config_version"],
        **info.get(row["well_id"], {"well_id": row["well_id"]}),
        "station_id": row["st_id"] or "",
        "station_sdmo_id": row["st_sdmo_id"] or "",
        "station_name": row["st_name"] or "",
        "station_code": row["st_code"] or "",
        "station_type_1900": row["st_type"] if row["st_type"] is not None else "",
        "station_active": row["st_active"] if row["st_active"] is not None else "",
        "station_ngdu_abai_id": row["st_ngdu_abai"] or "",
        "station_ngdu_name": row["st_ngdu_name"] or "",
        "r9_k": _get(p, "k"),
        "r9_k_alert": _get(p, "k_alert"),
        "r9_k_degrade": _get(p, "k_degrade"),
        "r9_base_k_p90": _get(p, "baseline", "k_p90"),
        "r9_base_p95_median": _get(p, "baseline", "p95_median"),
        "r9_base_days": _get(p, "baseline", "n_days"),
        "r9_branches": _get(p, "branches"),
        "r2_current_ratio": _get(p, "current_ratio"),
        "r2_base_moment": _get(p, "base_moment"),
        "r2_warn_threshold": _get(p, "warn_threshold"),
        "r2_alarm_threshold": _get(p, "alarm_threshold"),
        "r2_event_class": _get(p, "event_class"),
        "r2_failure_dt": _get(p, "failure_dt"),
        "r2_lead_time_hours": _get(p, "lead_time_hours"),
        "r2_low_confidence": _get(p, "low_confidence"),
        "r10_event": _get(p, "event"),
        "r10_klass": _get(p, "klass"),
        "r10_dev": _get(p, "dev"),
        "r10_prev_dev": _get(p, "prev_dev"),
        "r10_q_last": _get(p, "q_last"),
        "r10_rezhim": _get(p, "rezhim"),
        "r10_su": _get(p, "su"),
        "r10_age_d": _get(p, "age_d"),
        "r10_n_series": _get(p, "n_series"),
        "r10_series_from": _get(p, "series_from"),
        "r10_last_day": _get(p, "last_day"),
        "r10_note": _get(p, "note"),
        "payload_json": json.dumps(p, ensure_ascii=False, default=str),
    }


def _finding_row(row: Any, info: dict[int, dict]) -> dict:  # noqa: ANN401
    p = row["payload"] or {}
    return {
        "record_type": "r10_finding",
        "finding_id": row["id"],
        "detector_code": row["detector_code"],
        "detector_name": row["detector_name"],
        "fix_date": row["fix_date"].isoformat(),
        "kind": row["kind"],
        "title": row["title"],
        "config_version": row["config_version"],
        **info.get(row["well_id"], {"well_id": row["well_id"]}),
        "event": _get(p, "event"),
        "klass": _get(p, "klass"),
        "dev": _get(p, "dev"),
        "q_last": _get(p, "q_last"),
        "rezhim": _get(p, "rezhim"),
        "su": _get(p, "su"),
        "detail": _get(p, "detail"),
        "note": _get(p, "note"),
        "payload_json": json.dumps(p, ensure_ascii=False, default=str),
    }


async def export(out: Path) -> int:
    """Записать CSV в ``out``; вернуть число строк."""
    async with session_makers["app"]() as session:
        await session.execute(text("SET TRANSACTION READ ONLY"))
        incidents = (
            (
                await session.execute(
                    text(
                        "select i.*, d.name_ru detector_name, "
                        "d.source detector_source, "
                        "st.id st_id, st.sdmo_id st_sdmo_id, st.name st_name, "
                        "st.code st_code, st.type_1900 st_type, st.active st_active, "
                        "st.abai_ngdu_id st_ngdu_abai, so.name_ru st_ngdu_name "
                        "from detectors_incident i "
                        "join detectors_detector d on d.code = i.detector_code "
                        "left join telemetry_sdmo_station st on st.id = i.entity_id "
                        "left join org so on so.abai_id = st.abai_ngdu_id "
                        "order by i.well_id, i.opened_at",
                    ),
                )
            )
            .mappings()
            .all()
        )
        findings = (
            (
                await session.execute(
                    text(
                        "select f.*, d.name_ru detector_name from detectors_finding f "
                        "join detectors_detector d on d.code = f.detector_code "
                        "order by f.well_id, f.fix_date",
                    ),
                )
            )
            .mappings()
            .all()
        )
        info = await _well_info(
            session,
            {r["well_id"] for r in incidents} | {r["well_id"] for r in findings},
        )
        await session.rollback()

    rows = [_incident_row(r, info) for r in incidents]
    rows += [_finding_row(r, info) for r in findings]
    header: list[str] = []
    for row in rows:
        header += [key for key in row if key not in header]
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=header, delimiter=";", restval="")
        writer.writeheader()
        writer.writerows(rows)
    logger.info(
        "Detections exported to %s: %s rows (%s), %s columns",
        out,
        len(rows),
        dict(Counter((r["record_type"], r["detector_code"]) for r in rows)),
        len(header),
    )
    return len(rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="Куда писать CSV")
    args = parser.parse_args()
    asyncio.run(export(args.out))

"""Сохранение замера СПО из сырого payload Toucan: файлы, ``repairs_spo``, события.

Один код на оба пути получения payload: прямой RPC в Toucan
(``KbrsSPOFetcher``) и связывание замера, уже скачанного опросчиком КБРС
(``repairs.link_spo``). Файлы уезжают в бакет ремонтов под детерминированными
ключами ``spo/{well_id}/{measure_id}/...``: при перечитке выросшего замера
объекты перезаписываются, а строки ``files_file`` переиспользуются.
"""

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from io import BytesIO

from apps.files.dto.internal.repositories.file import CreateFileDTO
from apps.files.repositories.file import FileRepository
from apps.wells.dto.internal.repositories.spo import CreateSPODTO, UpdateSPODTO
from apps.wells.dto.internal.repositories.spo_event import CreateSPOEventDTO
from apps.wells.models.spo import SPO
from apps.wells.repositories.spo import SPORepository
from apps.wells.repositories.spo_event import SPOEventRepository
from core import get_logger
from shared.database.s3.storage import AiobotoFileStorage
from shared.integrations.kbrs.api.dtos import (
    MeasurementEventDto,
    MeasurementFullDto,
    MeasurementParsedDto,
    MeasurementPassportDto,
)

logger = get_logger(__name__)


def is_up_to_date(existing: SPO | None, *, raw_size: int) -> bool:
    """Замер уже сохранён в этом объёме: перезаливать и перечитывать незачем."""
    return (
        existing is not None
        and existing.raw_size == raw_size
        and existing.chart_file_id is not None
        and existing.chart_json_file_id is not None
        and existing.notes_file_id is not None
        and existing.passport_file_id is not None
    )


def _naive(value: datetime) -> datetime:
    return value.replace(tzinfo=None) if value.tzinfo is not None else value


@dataclass(slots=True)
class SpoMeasurementPersister:
    storage: AiobotoFileStorage
    file_repo: FileRepository
    spo_repo: SPORepository
    spo_event_repo: SPOEventRepository

    async def persist(  # noqa: PLR0913
        self,
        *,
        well_id: int,
        measure_id: int,
        raw_bytes: bytes,
        full: MeasurementFullDto,
        fallback_snapshot_time: datetime,
        existing: SPO | None = None,
    ) -> SPO | None:
        """Создать или обновить СПО по разобранному замеру; ``None`` — не удалось."""
        parsed = full.chart
        snapshot_time = _naive(parsed.start or fallback_snapshot_time)

        if existing is None:
            existing = await self.spo_repo.get_by_kbrs_measure_id(
                measure_id,
                well_id=well_id,
            )
        if existing is None:
            existing = await self.spo_repo.get_by_well_id_and_snapshot_time(
                well_id=well_id,
                snapshot_time=snapshot_time,
            )
        if is_up_to_date(existing, raw_size=len(raw_bytes)):
            return existing

        # Строки файлов переиспользуются, только если они уже указывают на
        # ключи этого замера; СПО, найденное по времени снимка от другого
        # замера, получает новые строки.
        reuse = (
            existing if existing and existing.kbrs_measure_id == measure_id else None
        )
        prefix = f"spo/{well_id}/{measure_id}"
        master_id = await self._upload(
            raw_bytes,
            f"{prefix}/raw.bin",
            reuse.file_id if reuse else None,
        )
        if master_id is None:
            return None
        values = {
            "file_id": master_id,
            "chart_file_id": await self._upload(
                _render_csv(parsed),
                f"{prefix}/chart.csv",
                reuse.chart_file_id if reuse else None,
            ),
            "chart_json_file_id": await self._upload(
                _render_chart_json(parsed),
                f"{prefix}/chart.json",
                reuse.chart_json_file_id if reuse else None,
            ),
            "notes_file_id": await self._upload(
                _render_notes(parsed),
                f"{prefix}/notes.json",
                reuse.notes_file_id if reuse else None,
            ),
            "passport_file_id": await self._upload(
                _render_passport(full.details.passport),
                f"{prefix}/passport.json",
                reuse.passport_file_id if reuse else None,
            ),
            "kbrs_measure_id": measure_id,
            "raw_size": len(raw_bytes),
        }

        if existing is None:
            spo = await self.spo_repo.create(
                CreateSPODTO(snapshot_time=snapshot_time, well_id=well_id, **values),
            )
        else:
            spo = await self.spo_repo.update_by_id(
                existing.id,
                UpdateSPODTO(snapshot_time=snapshot_time, **values),
            )
        await self._replace_events(spo.id, full.details.events)
        return spo

    async def _replace_events(
        self,
        spo_id: int,
        events: Sequence[MeasurementEventDto],
    ) -> None:
        dtos = [
            CreateSPOEventDTO(
                spo_id=spo_id,
                offset=event.offset,
                time_text=event.time_text,
                code=event.code,
                text=event.text,
                raw_text=event.raw_text,
            )
            for event in events
        ]
        await self.spo_event_repo.replace_for_spo(spo_id, dtos)
        logger.info("Persisted %s SPO events for spo_id=%s.", len(dtos), spo_id)

    async def _upload(
        self,
        payload: bytes,
        s3_key: str,
        existing_file_id: int | None,
    ) -> int | None:
        try:
            await self.storage.upload_file(BytesIO(payload), s3_key)
        except Exception:
            logger.exception("Failed to upload SPO file to S3 (key=%s).", s3_key)
            return None
        if existing_file_id is not None:
            return existing_file_id
        file_row = await self.file_repo.create(CreateFileDTO(file=s3_key))
        return file_row.id


def _render_csv(parsed: MeasurementParsedDto) -> bytes:
    header = "timestamp,datetime,hook_weight_t,h2s_mg_m3,ch4_percent\n"
    lines = [
        header,
        *(
            f"{row.timestamp},"
            f"{row.datetime.isoformat() if row.datetime else ''},"
            f"{row.hook_weight_t if row.hook_weight_t is not None else ''},"
            f"{row.h2s_mg_m3 if row.h2s_mg_m3 is not None else ''},"
            f"{row.ch4_percent if row.ch4_percent is not None else ''}\n"
            for row in parsed.rows
        ),
    ]
    return "".join(lines).encode("utf-8")


def _render_chart_json(parsed: MeasurementParsedDto) -> bytes:
    # Компактный формат для чартов: ``[[timestamp_ms, value], ...]`` на канал,
    # пропуски остаются ``null``, чтобы каналы не съезжали друг относительно
    # друга.
    series_specs = (
        ("hook_weight_t", "т"),
        ("h2s_mg_m3", "мг/м³"),
        ("ch4_percent", "%"),
    )
    series = [
        {
            "key": key,
            "unit": unit,
            "points": [
                [row.timestamp * 1000, getattr(row, key)] for row in parsed.rows
            ],
        }
        for key, unit in series_specs
    ]
    payload = {
        "start": parsed.start.isoformat() if parsed.start else None,
        "end": parsed.end.isoformat() if parsed.end else None,
        "row_count": len(parsed.rows),
        "series": series,
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _render_notes(parsed: MeasurementParsedDto) -> bytes:
    meta = {
        "magic": parsed.magic,
        "records_count": parsed.records_count,
        "row_count": len(parsed.rows),
        "channels": parsed.channels,
        "start": parsed.start.isoformat() if parsed.start else None,
        "end": parsed.end.isoformat() if parsed.end else None,
        "header_hint": parsed.header_ascii_hint,
    }
    return json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8")


def _render_passport(passport: MeasurementPassportDto) -> bytes:
    # В ``values`` бывают datetime и другие не-JSON типы — ``default=str``.
    return json.dumps(
        asdict(passport),
        ensure_ascii=False,
        indent=2,
        default=str,
    ).encode("utf-8")

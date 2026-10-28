"""ИИ-разбор динамограммы v3: подготовка файла, промпт, нормализация ответа,
правила повторов и общий анализатор двух писателей."""

import asyncio
import json
from datetime import date, datetime, timedelta
from io import BytesIO
from types import SimpleNamespace

import pypdfium2 as pdfium
import pytest
from botocore.exceptions import ClientError
from langchain.messages import AIMessage
from PIL import Image

from apps.repairs.models.analytics import AI_STATUS_COMPLETED, AI_STATUS_FAILED
from apps.repairs.repositories.ai_results import version_number
from apps.repairs.tasks.fill_analytics.ai import dynamogram_image
from apps.repairs.tasks.fill_analytics.ai.dynamogram_analyzer import (
    FAILED_RETRY_AFTER,
    INPUT_REJECTED_PREFIX,
    DynamogramAnalyzer,
    needs_analysis,
)
from apps.repairs.tasks.fill_analytics.ai.dynamogram_image import (
    UnsupportedDynamogramFileError,
    detect_kind,
    prepare_image,
)
from apps.repairs.tasks.fill_analytics.ai.dynamogram_processor import (
    DynamogramAIProcessor,
    DynamogramProcessingInput,
    build_parsed,
    build_prompt,
    coef_to_pct,
)
from apps.wells.dto.internal.dynamogram import DynamogramGdisDTO, WellDynamogramAIDTO
from apps.wells.services.gdis import DynamogramGdisService
from shared.database.s3.storage import FileNotExistError

NOW = datetime(2026, 10, 27, 12, 0)  # noqa: DTZ001
SNAPSHOT = datetime(2026, 10, 20)  # noqa: DTZ001
HTML_STUB = b'<!doctype html>\n<html lang="ru">\n<head>\n' + b" " * 200


def _png() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (4, 4), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def _pdf() -> bytes:
    pdf = pdfium.PdfDocument.new()
    pdf.new_page(200, 100)
    buffer = BytesIO()
    pdf.save(buffer)
    pdf.close()
    return buffer.getvalue()


def _gdis(**overrides: object) -> DynamogramGdisDTO:
    values = {
        "meas_date": SNAPSHOT.date(),
        "stroke_length_m": 2.5,
        "strokes_per_min": 4.3,
        "load_max": 2.6,
        "load_min": 1.5,
        "fill_coef": 0.859,
        "pump_feed_coef": 0.6689,
        "operator_conclusion": "НРН",
    }
    return DynamogramGdisDTO(**(values | overrides))


MODEL_OUTPUT = {
    "state": "normal",
    "confidence": 93,
    "score": "88",
    "fill_pct_estimate": 80,
    "pump_efficiency_pct_estimate": "70,5",
    "observations": ["Линии хода вверх и вниз параллельны", " "],
    "issues": [],
    "recommendations": "Режим не менять",
    "verdict": "Отклонений не обнаружено.",
}


# --- файл ---------------------------------------------------------------------


def test_detect_kind_by_signature_not_extension() -> None:
    assert detect_kind(_png()) == "png"
    assert detect_kind(b"\xff\xd8\xff\xe0rest") == "jpeg"
    assert detect_kind(HTML_STUB) == "html"
    assert detect_kind(b"") == "empty"
    assert detect_kind(_pdf()) == "pdf"
    assert detect_kind(b"PK\x03\x04docx") == "unknown"


def test_prepare_image_passes_images_and_renders_pdf() -> None:
    png = _png()
    assert prepare_image(png) == (png, "image/png")

    rendered, mime = prepare_image(_pdf())
    assert mime == "image/png"
    assert detect_kind(rendered) == "png"


@pytest.mark.parametrize(
    ("payload", "kind"),
    [
        (HTML_STUB, "html"),
        (b"", "empty"),
        (b"%PDF-broken", "pdf_broken"),
        # Сигнатура PNG верная, файл обрезан — модель ответила бы 400.
        (_png()[:30], "image_broken"),
    ],
)
def test_prepare_image_rejects_unreadable(payload: bytes, kind: str) -> None:
    with pytest.raises(UnsupportedDynamogramFileError) as exc:
        prepare_image(payload)
    assert exc.value.kind == kind


def test_pdf_page_failure_is_unsupported_file(monkeypatch: pytest.MonkeyPatch) -> None:
    class _BrokenPagePdf:
        def __init__(self, _payload: bytes) -> None: ...

        def __len__(self) -> int:
            return 1

        def __getitem__(self, _index: int) -> object:
            msg = "Failed to load page."
            raise pdfium.PdfiumError(msg)

        def close(self) -> None: ...

    monkeypatch.setattr(dynamogram_image.pdfium, "PdfDocument", _BrokenPagePdf)
    with pytest.raises(UnsupportedDynamogramFileError) as exc:
        prepare_image(b"%PDF-1.4 ...")
    assert exc.value.kind == "pdf_broken"


def test_huge_pdf_page_is_rendered_within_limit() -> None:
    pdf = pdfium.PdfDocument.new()
    pdf.new_page(14_000, 7_000)
    buffer = BytesIO()
    pdf.save(buffer)
    pdf.close()

    rendered, _mime = prepare_image(buffer.getvalue())
    with Image.open(BytesIO(rendered)) as image:
        assert max(image.size) <= dynamogram_image.PDF_MAX_SIDE_PX


# --- промпт и ответ -------------------------------------------------------------


def _item(gdis: DynamogramGdisDTO | None = None) -> DynamogramProcessingInput:
    return DynamogramProcessingInput(
        dynamogram=SimpleNamespace(id=1, snapshot_time=SNAPSHOT),
        s3_key="dynamograms/1/a.png",
        image_bytes=_png(),
        gdis=gdis,
    )


def test_prompt_has_gdis_values_and_no_repair_role() -> None:
    prompt = build_prompt(_item(_gdis()))
    assert "20.10.2026" in prompt
    assert "Длина хода, м: 2.5" in prompt
    assert "Наполнение (K наполнения): 85.9 %" in prompt
    assert "КПД (K подачи насоса): 66.89 %" in prompt
    # Разбор — свойство динамограммы, а не роли в ремонте.
    assert "ДО" not in prompt
    assert "ПОСЛЕ" not in prompt
    # Заключение оператора модели не показываем: по нему проверяем модель.
    assert "НРН" not in prompt


def test_prompt_without_gdis() -> None:
    assert "Исследования ГДИС за этот день нет" in build_prompt(_item())


def test_build_parsed_prefers_gdis_numbers() -> None:
    parsed = build_parsed(MODEL_OUTPUT, _gdis())

    assert parsed["state"] == "normal"
    assert parsed["condition"] == "Норма"
    assert parsed["severity"] == "ok"
    assert parsed["confidence"] == 93.0
    assert parsed["score"] == 88.0
    assert (parsed["fill_pct"], parsed["fill_source"]) == (85.9, "gdis")
    assert parsed["fill_pct_estimate"] == 80.0
    assert (parsed["pump_efficiency_pct"], parsed["pump_efficiency_source"]) == (
        66.89,
        "gdis",
    )
    assert parsed["pump_efficiency_pct_estimate"] == 70.5
    assert parsed["observations"] == ["Линии хода вверх и вниз параллельны"]
    assert parsed["recommendations"] == ["Режим не менять"]
    assert parsed["gdis"]["operator_conclusion"] == "НРН"
    # Ключи v2 на месте: их читают KPI, общий вердикт и экран ремонта.
    assert {"pump_efficiency_pct", "fill_pct", "condition", "score", "issues"} <= (
        parsed.keys()
    )


def test_build_parsed_falls_back_to_model_estimate() -> None:
    parsed = build_parsed(MODEL_OUTPUT, _gdis(fill_coef=None, pump_feed_coef=None))
    assert (parsed["fill_pct"], parsed["fill_source"]) == (80.0, "model")
    assert (parsed["pump_efficiency_pct"], parsed["pump_efficiency_source"]) == (
        70.5,
        "model",
    )

    parsed = build_parsed({"state": "gas"}, None)
    assert (parsed["fill_pct"], parsed["fill_source"]) == (None, None)
    assert parsed["gdis"] is None
    assert parsed["severity"] == "warning"


def test_build_parsed_guards_model_output() -> None:
    parsed = build_parsed(
        {"state": "Неизвестно", "confidence": 140, "score": True, "verdict": "  "},
        None,
    )
    assert parsed["state"] == "other"
    assert parsed["confidence"] is None
    assert parsed["score"] is None
    assert parsed["verdict"] is None
    assert parsed["observations"] == []


@pytest.mark.parametrize(
    ("coef", "pct"),
    [(0.859, 85.9), (1.1, 110.0), (85.9, 85.9), (13.2, 13.2)],
)
def test_coef_to_pct(coef: float, pct: float) -> None:
    assert coef_to_pct(coef) == pct


# Ноль — заглушка «не измерено»; 1.2–5 — ни доля, ни процент.
@pytest.mark.parametrize("coef", [None, -1.0, 0.0, 1.5, 4.9, 920.0])
def test_coef_to_pct_rejects_garbage(coef: float | None) -> None:
    assert coef_to_pct(coef) is None


class _Agent:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = 0

    async def ainvoke(self, *, input: dict) -> dict:  # noqa: A002
        self.calls += 1
        return {"messages": [*input["messages"], AIMessage(content=self.content)]}


def test_processor_enriches_json_answer() -> None:
    processor = DynamogramAIProcessor(_Agent(json.dumps(MODEL_OUTPUT)), "m")
    result = asyncio.run(processor.process(_item(_gdis())))

    assert result.status == AI_STATUS_COMPLETED
    assert result.prompt_version == "v3"
    assert result.result["parsed"]["fill_source"] == "gdis"
    assert json.loads(result.result["raw"]) == MODEL_OUTPUT


def test_processor_fails_on_non_json_answer() -> None:
    processor = DynamogramAIProcessor(_Agent("Насос в норме."), "m")
    result = asyncio.run(processor.process(_item()))

    assert result.status == AI_STATUS_FAILED
    assert result.result["raw"] == "Насос в норме."


# --- когда разбирать --------------------------------------------------------------


def _row(**fields: object) -> SimpleNamespace:
    values = {
        "status": AI_STATUS_COMPLETED,
        "prompt_version": "v3",
        "error": None,
        "processed_at": NOW - timedelta(hours=1),
    }
    return SimpleNamespace(**(values | fields))


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (None, True),
        (_row(), False),
        (_row(prompt_version="v2"), True),
        (_row(prompt_version="v4"), False),
        (_row(status="pending"), True),
        (_row(status=AI_STATUS_FAILED), False),
        (
            _row(status=AI_STATUS_FAILED, processed_at=NOW - FAILED_RETRY_AFTER),
            True,
        ),
        (
            _row(
                status=AI_STATUS_FAILED,
                error=f"{INPUT_REJECTED_PREFIX}html",
                processed_at=NOW - timedelta(days=30),
            ),
            False,
        ),
        (
            _row(
                status=AI_STATUS_FAILED,
                prompt_version="v2",
                error=f"{INPUT_REJECTED_PREFIX}html",
            ),
            True,
        ),
    ],
)
def test_needs_analysis(row: SimpleNamespace | None, *, expected: bool) -> None:
    assert needs_analysis(row, prompt_version="v3", now=NOW) is expected


def test_version_number() -> None:
    assert version_number("v3") == 3
    assert version_number("v12") > version_number("v3")
    assert version_number(None) == 0
    assert version_number("draft") == 0
    assert version_number("v2.1") < version_number("v3")


# --- анализатор ---------------------------------------------------------------------


class _AIRepo:
    def __init__(
        self,
        existing: SimpleNamespace | None = None,
        *,
        locked: bool = False,
    ) -> None:
        self.existing = existing
        self.locked = locked
        self.upserted: list = []
        self.lock_waits: list[bool] = []

    async def lock_dynamogram(self, _dynamogram_id: int, *, wait: bool) -> bool:
        self.lock_waits.append(wait)
        return wait or not self.locked

    async def get_by_dynamogram_id(self, _dynamogram_id: int) -> SimpleNamespace | None:
        return self.existing

    async def upsert(self, data: object) -> object:
        self.upserted.append(data)
        return data


class _Files:
    async def get_by_id(self, file_id: int) -> SimpleNamespace:
        return SimpleNamespace(id=file_id, file=f"dynamograms/{file_id}/x.PNG")


class _Storage:
    def __init__(self, payload: bytes | str) -> None:
        # str — код ошибки S3 вместо файла.
        self.payload = payload

    async def download_file(self, _key: str) -> BytesIO:
        if isinstance(self.payload, str):
            error = ClientError({"Error": {"Code": self.payload}}, "GetObject")
            raise FileNotExistError from error
        return BytesIO(self.payload)


class _Gdis:
    def __init__(self) -> None:
        self.asked: list[tuple[int, date]] = []

    async def get_for_dynamogram(self, abai_well_id: int, meas_date: date) -> object:
        self.asked.append((abai_well_id, meas_date))
        return _gdis()


def _analyzer(
    payload: bytes | str,
    *,
    existing: SimpleNamespace | None = None,
    locked: bool = False,
) -> tuple[DynamogramAnalyzer, _AIRepo, _Agent, _Gdis]:
    agent = _Agent(json.dumps(MODEL_OUTPUT))
    repo, gdis = _AIRepo(existing, locked=locked), _Gdis()
    analyzer = DynamogramAnalyzer(
        processor=DynamogramAIProcessor(agent, "m"),
        ai_repo=repo,
        file_repo=_Files(),
        storage=_Storage(payload),
        gdis_service=gdis,
    )
    return analyzer, repo, agent, gdis


DYNAMOGRAM = SimpleNamespace(id=49515, file_id=52637, snapshot_time=SNAPSHOT)


def test_analyzer_runs_llm_with_gdis_and_upserts() -> None:
    analyzer, repo, agent, gdis = _analyzer(_png())
    row, _usage = asyncio.run(analyzer.analyze(DYNAMOGRAM, abai_well_id=9980))

    assert agent.calls == 1
    assert gdis.asked == [(9980, SNAPSHOT.date())]
    assert row.status == AI_STATUS_COMPLETED
    assert row.prompt_version == "v3"
    assert row.result["parsed"]["fill_source"] == "gdis"
    assert len(repo.upserted) == 1


def test_analyzer_rejects_html_stub_without_llm() -> None:
    analyzer, repo, agent, _gdis_service = _analyzer(HTML_STUB)
    row, usage = asyncio.run(analyzer.analyze(DYNAMOGRAM, abai_well_id=9980))

    assert agent.calls == 0
    assert row.status == AI_STATUS_FAILED
    assert row.error == f"{INPUT_REJECTED_PREFIX}html"
    assert row.prompt_version == "v3"
    assert usage.total_tokens == 0
    assert len(repo.upserted) == 1


def test_analyzer_keeps_current_result() -> None:
    current = _row()
    analyzer, repo, agent, _gdis_service = _analyzer(_png(), existing=current)
    row, _usage = asyncio.run(analyzer.analyze(DYNAMOGRAM, abai_well_id=9980))

    assert row is current
    assert agent.calls == 0
    assert repo.upserted == []


def test_analyzer_marks_missing_s3_object_as_bad_input() -> None:
    analyzer, repo, agent, _gdis_service = _analyzer("NoSuchKey")
    row, _usage = asyncio.run(analyzer.analyze(DYNAMOGRAM, abai_well_id=9980))

    assert agent.calls == 0
    assert row.error == f"{INPUT_REJECTED_PREFIX}missing in S3"
    assert len(repo.upserted) == 1


@pytest.mark.parametrize("code", ["AccessDenied", "InvalidAccessKeyId", "503"])
def test_analyzer_does_not_persist_s3_outage(code: str) -> None:
    # Сбой S3 — не свойство файла: строку не пишем, иначе после деплоя с
    # неверными ключами весь фонд застрял бы в «битых файлах».
    analyzer, repo, agent, _gdis_service = _analyzer(code)
    with pytest.raises(FileNotExistError):
        asyncio.run(analyzer.analyze(DYNAMOGRAM, abai_well_id=9980))

    assert agent.calls == 0
    assert repo.upserted == []


def test_fund_pass_skips_dynamogram_locked_by_repair_analytics() -> None:
    analyzer, repo, agent, _gdis_service = _analyzer(_png(), locked=True)
    row, usage = asyncio.run(
        analyzer.analyze(DYNAMOGRAM, abai_well_id=9980, wait_for_lock=False),
    )

    assert row is None
    assert usage.total_tokens == 0
    assert agent.calls == 0
    assert repo.upserted == []


def test_repair_analytics_waits_for_lock() -> None:
    analyzer, repo, agent, _gdis_service = _analyzer(_png(), locked=True)
    row, _usage = asyncio.run(analyzer.analyze(DYNAMOGRAM, abai_well_id=9980))

    assert repo.lock_waits == [True]
    assert row.status == AI_STATUS_COMPLETED
    assert agent.calls == 1


# --- ГДИС на день динамограммы ------------------------------------------------------


class _Metrics:
    async def map_abai_ids_by_codes(self, codes: list[str]) -> dict[str, int]:
        known = {"PLST": 319, "ADMCF": 331, "PDCF": 332}
        return {code: known[code] for code in codes if code in known}


class _Studies:
    async def list_by_abai_well_id_and_date(
        self,
        _abai_well_id: int,
        _meas_date: date,
    ) -> list[SimpleNamespace]:
        # Поздние сверху, как в репозитории.
        return [
            SimpleNamespace(abai_id=20, conclusion_text="  "),
            SimpleNamespace(abai_id=10, conclusion_text="Влияние газа"),
        ]


class _Values:
    async def list_by_gdis_abai_ids(self, _ids: list[int]) -> list[SimpleNamespace]:
        # (abai_id, исследование, метрика, значение)
        rows = [
            (1, 10, 319, 2.1),
            (2, 10, 331, 0.5),
            (3, 20, 331, 0.7),
            (4, 20, 332, None),
            (5, 20, 999, 1.0),
        ]
        return [
            SimpleNamespace(
                abai_id=abai_id,
                gdis_current_abai_id=study,
                metric_abai_id=metric,
                value_double=value,
            )
            for abai_id, study, metric, value in rows
        ]


def test_gdis_service_merges_studies_of_the_day() -> None:
    service = DynamogramGdisService(
        gdis_metric_repository=_Metrics(),
        gdis_current_repository=_Studies(),
        gdis_current_value_repository=_Values(),
    )
    gdis = asyncio.run(service.get_for_dynamogram(9980, SNAPSHOT.date()))

    assert gdis.fill_coef == 0.7  # из позднего исследования
    assert gdis.stroke_length_m == 2.1  # есть только в раннем
    assert gdis.pump_feed_coef is None
    assert gdis.operator_conclusion == "Влияние газа"


# --- выдача в /wells/{id}/dynamograms ------------------------------------------------


def test_well_dynamogram_ai_dto_reads_v3_and_v2() -> None:
    v3 = WellDynamogramAIDTO.from_result(
        status=AI_STATUS_COMPLETED,
        prompt_version="v3",
        model_name="m",
        processed_at=NOW,
        error=None,
        result={"parsed": build_parsed(MODEL_OUTPUT, _gdis())},
    )
    assert v3.state == "normal"
    assert v3.confidence == 93.0
    assert v3.fill_source == "gdis"
    assert v3.gdis.stroke_length_m == 2.5

    v2 = WellDynamogramAIDTO.from_result(
        status=AI_STATUS_COMPLETED,
        prompt_version="v2",
        model_name="m",
        processed_at=NOW,
        error=None,
        result={
            "parsed": {
                "pump_efficiency_pct": "75.5",
                "fill_pct": 80,
                "condition": "норма",
                "issues": ["x"],
            },
        },
    )
    assert v2.pump_efficiency_pct == 75.5
    assert v2.condition == "норма"
    assert v2.state is None
    assert v2.observations == []

    failed = WellDynamogramAIDTO.from_result(
        status=AI_STATUS_FAILED,
        prompt_version="v3",
        model_name=None,
        processed_at=NOW,
        error=f"{INPUT_REJECTED_PREFIX}html",
        result=None,
    )
    assert failed.status == AI_STATUS_FAILED
    assert failed.verdict is None

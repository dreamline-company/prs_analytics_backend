"""Письмо с ведомостями: получатели из настроек, тема, тело, вложения."""

from datetime import date

import pytest

from apps.detectors.dto.internal.daily_sheet import (
    DailySheetCoverageDTO,
    DailySheetDTO,
    DailySheetTopItemDTO,
)
from apps.detectors.services.daily_sheet.mail import (
    LetterInput,
    MissingSheet,
    SheetAttachment,
    attachment_filename,
    build_message,
    parse_recipients,
    parse_recipients_by_ngdu,
    recipients_for,
    subject_for,
)


def _sheet(code: str, rows: int) -> DailySheetDTO:
    return DailySheetDTO(
        detector_code=code,
        detector_name_ru="Перекос нагрузки" if code == "R9" else "Обрыв штанги",
        ngdu_id=5,
        ngdu_name="Кайнармунайгаз",
        abai_ngdu_id=12,
        sheet_date=date(2026, 9, 20),
        status="completed",
        rows_count=rows,
        coverage=DailySheetCoverageDTO(stations_total=146, stations_reporting=118),
        built_at=None,
        config_version="sheet-v1",
        file_id=60,
        top=[
            DailySheetTopItemDTO(
                rank=1,
                well_name="UZK_0377",
                probability_percent=85,
                text="UZK_0377 — 85%. Жидкость 19.6 при режиме 25.",
            ),
        ]
        if rows
        else [],
    )


def test_parse_recipients_dedupes_and_splits_on_any_separator() -> None:
    assert parse_recipients(" a@x.kz, b@x.kz;a@x.kz\nc@x.kz ") == [
        "a@x.kz",
        "b@x.kz",
        "c@x.kz",
    ]
    assert parse_recipients("") == []


def test_parse_recipients_by_ngdu_accepts_code_or_abai_id() -> None:
    parsed = parse_recipients_by_ngdu(
        '{"kmg": ["a@x.kz"], "11": "b@x.kz, c@x.kz", "DMG": []}',
    )
    assert parsed == {12: ["a@x.kz"], 11: ["b@x.kz", "c@x.kz"], 9: []}
    assert parse_recipients_by_ngdu("") == {}
    with pytest.raises(KeyError):
        parse_recipients_by_ngdu('{"XXX": ["a@x.kz"]}')
    with pytest.raises(TypeError, match="JSON object"):
        parse_recipients_by_ngdu('["a@x.kz"]')


def test_recipients_for_prefers_ngdu_list_even_if_empty() -> None:
    default = ["all@x.kz"]
    by_ngdu = {12: ["kmg@x.kz"], 9: []}
    assert recipients_for(12, default=default, by_ngdu=by_ngdu) == ["kmg@x.kz"]
    assert recipients_for(9, default=default, by_ngdu=by_ngdu) == []
    assert recipients_for(11, default=default, by_ngdu=by_ngdu) == ["all@x.kz"]


def test_attachment_filename_strips_path_and_build_stamp() -> None:
    key = (
        "detectors/daily_sheet/R9/12/2026-09-20/"
        "20260921T075001_Vedomost_R9_KMG_2026-09-20.docx"
    )
    assert attachment_filename(key) == "Vedomost_R9_KMG_2026-09-20.docx"
    assert attachment_filename("plain.docx") == "plain.docx"


def test_build_message_has_attachments_and_mentions_missing_sheet() -> None:
    letter = LetterInput(
        sheet_date=date(2026, 9, 20),
        ngdu_name="Кайнармунайгаз",
        attachments=[
            SheetAttachment(
                sheet=_sheet("R9", 4),
                filename="Vedomost_R9_KMG_2026-09-20.docx",
                payload=b"PK\x03\x04docx",
            ),
        ],
        missing=[MissingSheet("R2", "за сутки нет телеметрии СДМО (0 из 146 станций)")],
    )
    message = build_message(letter, recipients=["a@x.kz", "b@x.kz"], sender="prs@x.kz")
    assert message["Subject"] == subject_for(date(2026, 9, 20), "Кайнармунайгаз")
    assert "20.09.2026" in message["Subject"]
    assert message["To"] == "a@x.kz, b@x.kz"
    assert message["From"] == "prs@x.kz"
    body = message.get_body(preferencelist=("plain",)).get_content()
    assert "R9 «Перекос нагрузки»: строк 4" in body
    assert "118 из 146 станций" in body
    assert "1. UZK_0377 — 85%" in body
    assert "R2: ведомость не сформирована — за сутки нет телеметрии" in body
    attachments = list(message.iter_attachments())
    assert [a.get_filename() for a in attachments] == [
        "Vedomost_R9_KMG_2026-09-20.docx",
    ]
    assert attachments[0].get_content_type() == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert attachments[0].get_payload(decode=True) == b"PK\x03\x04docx"

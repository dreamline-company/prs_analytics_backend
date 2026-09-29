"""Письмо с суточными ведомостями R2/R9 по НГДУ — чистая логика.

Получатели берутся из настроек: общий список и переопределение по НГДУ.
Письмо одно на НГДУ и дату, внутри вложения по каждому правилу; правило без
ведомости (нет телеметрии за сутки) описывается в тексте, а не молча
пропускается — иначе отсутствие файла прочитают как «отклонений нет».
"""

import json
import re
from dataclasses import dataclass, field
from datetime import date
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from apps.detectors.dto.internal.daily_sheet import DailySheetDTO
from apps.detectors.services.daily_sheet.config import SHEET_DETECTOR_CODES
from shared.constants.ngdu import AbaiNGDUIDsEnum

_SPLIT_RE = re.compile(r"[,\s;]+")
_DOCX_MIME = (
    "application",
    "vnd.openxmlformats-officedocument.wordprocessingml.document",
)
_MAX_TOP_IN_BODY = 3


def parse_recipients(raw: str) -> list[str]:
    """«a@x, b@x; c@x» -> список без пустых и дублей, порядок сохранён."""
    seen: dict[str, None] = {}
    for item in _SPLIT_RE.split(raw or ""):
        address = item.strip()
        if address:
            seen.setdefault(address, None)
    return list(seen)


def parse_recipients_by_ngdu(raw: str) -> dict[int, list[str]]:
    """JSON {"KMG": [...], "12": [...]} -> {abai_ngdu_id: [...]}.

    Ключ — код ``AbaiNGDUIDsEnum`` или его abai id; значение — список либо
    строка через запятую. Неизвестный ключ — ошибка конфигурации, лучше упасть
    при старте таска, чем молча слать не туда.
    """
    if not raw or not raw.strip():
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        msg = "DAILY_SHEET_MAIL_TO_BY_NGDU must be a JSON object"
        raise TypeError(msg)
    result: dict[int, list[str]] = {}
    for key, value in parsed.items():
        ngdu = _ngdu_from_key(str(key))
        addresses = value if isinstance(value, str) else ",".join(value)
        result[ngdu] = parse_recipients(addresses)
    return result


def _ngdu_from_key(key: str) -> int:
    key = key.strip()
    if key.isdigit():
        return AbaiNGDUIDsEnum(int(key)).value
    return AbaiNGDUIDsEnum[key.upper()].value


def recipients_for(
    abai_ngdu_id: int,
    *,
    default: list[str],
    by_ngdu: dict[int, list[str]],
) -> list[str]:
    """Список НГДУ, если задан (даже пустой — явное «не слать»), иначе общий."""
    if abai_ngdu_id in by_ngdu:
        return by_ngdu[abai_ngdu_id]
    return default


@dataclass(slots=True)
class SheetAttachment:
    sheet: DailySheetDTO
    filename: str
    payload: bytes


@dataclass(slots=True)
class MissingSheet:
    detector_code: str
    reason: str


@dataclass(slots=True)
class LetterInput:
    sheet_date: date
    ngdu_name: str
    attachments: list[SheetAttachment] = field(default_factory=list)
    missing: list[MissingSheet] = field(default_factory=list)


def subject_for(
    sheet_date: date,
    ngdu_name: str,
    codes: tuple[str, ...] = ("R2", "R9"),
) -> str:
    return (
        f"Суточная ведомость отклонений {'/'.join(codes)} за "
        f"{sheet_date:%d.%m.%Y} — НГДУ «{ngdu_name}»"
    )


def letter_codes(letter: LetterInput) -> tuple[str, ...]:
    """Правила письма (вложенные и несформированные) в порядке ведомостей."""
    codes = {item.sheet.detector_code for item in letter.attachments}
    codes |= {item.detector_code for item in letter.missing}
    order = {code: index for index, code in enumerate(SHEET_DETECTOR_CODES)}
    return tuple(sorted(codes, key=lambda code: (order.get(code, len(order)), code)))


def body_for(letter: LetterInput) -> str:
    lines = [
        f"НГДУ «{letter.ngdu_name}», сутки {letter.sheet_date:%d.%m.%Y}.",
        "Ведомости во вложении, по одному файлу на правило.",
        "",
    ]
    for item in letter.attachments:
        sheet = item.sheet
        rule = sheet.detector_code
        if sheet.detector_name_ru:
            rule += f" «{sheet.detector_name_ru}»"
        lines.append(f"{rule}: строк {sheet.rows_count}, файл {item.filename}.")
        coverage = sheet.coverage
        if coverage is not None and coverage.source == "cits":
            lines.append(
                f"  Охват: замеры ЦИТС за сутки есть у {coverage.stations_reporting} "
                f"из {coverage.stations_total} скважин.",
            )
        elif coverage is not None:
            lines.append(
                f"  Охват: телеметрию дали {coverage.stations_reporting} из "
                f"{coverage.stations_total} станций.",
            )
        lines.extend(
            f"  {top.rank}. {top.text}" for top in sheet.top[:_MAX_TOP_IN_BODY]
        )
        lines.append("")
    for missing in letter.missing:
        lines.append(
            f"{missing.detector_code}: ведомость не сформирована — {missing.reason}.",
        )
        lines.append("")
    lines.append(
        "Письмо сформировано автоматизированной системой диагностики по данным "
        "СДМО, ТМ, ABAI. Заполненную ведомость верните диспетчеру системы.",
    )
    return "\n".join(lines)


def build_message(
    letter: LetterInput,
    *,
    recipients: list[str],
    sender: str | None = None,
) -> EmailMessage:
    message = EmailMessage()
    codes = letter_codes(letter)
    message["Subject"] = (
        subject_for(letter.sheet_date, letter.ngdu_name, codes)
        if codes
        else subject_for(letter.sheet_date, letter.ngdu_name)
    )
    message["To"] = ", ".join(recipients)
    if sender:
        message["From"] = sender
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain="prs-analytics")
    message.set_content(body_for(letter), charset="utf-8")
    maintype, subtype = _DOCX_MIME
    for item in letter.attachments:
        message.add_attachment(
            item.payload,
            maintype=maintype,
            subtype=subtype,
            filename=item.filename,
        )
    return message


def attachment_filename(file_key: str) -> str:
    """Имя вложения из ключа S3: без пути и без временной метки сборки."""
    name = file_key.rsplit("/", 1)[-1]
    return re.sub(r"^\d{8}T\d{6}_", "", name)

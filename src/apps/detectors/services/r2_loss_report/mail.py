"""Письмо с отчётом «Обрыв штанг» — чистая логика.

Отчёт один на все НГДУ, поэтому и письмо одно: получают все, кому уходят
суточные ведомости (общий список и списки по НГДУ вместе).
"""

from collections.abc import Iterable
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from apps.detectors.dto.internal.r2_loss_report import R2LossReportDTO
from apps.detectors.services.r2_loss_report.rows import num

_XLSX_MIME = ("application", "vnd.openxmlformats-officedocument.spreadsheetml.sheet")
_TOP_IN_BODY = 5


def all_recipients(
    default: Iterable[str],
    by_ngdu: dict[int, list[str]],
) -> list[str]:
    """Общий список и все списки по НГДУ, без дублей, порядок сохранён."""
    seen: dict[str, None] = {}
    for address in [*default, *(a for items in by_ngdu.values() for a in items)]:
        seen.setdefault(address, None)
    return list(seen)


def attachment_name(report: R2LossReportDTO) -> str:
    return f"Отчет_Обрыв_Штанг_{report.report_date:%Y-%m-%d}.xlsx"


def subject_for(report: R2LossReportDTO) -> str:
    return (
        f"Обрыв штанг на {report.report_date:%d.%m.%Y}: "
        f"{report.wells_count} скв., недобор {num(report.total_oil_loss)} т/сут"
    )


def body_for(report: R2LossReportDTO) -> str:
    lines = [
        f"Отчёт «Обрыв штанг» на {report.report_date:%d.%m.%Y}.",
        "",
    ]
    if not report.rows:
        lines.append(
            "Скважин с сигналом R2 без начатого ремонта на конец суток нет.",
        )
    else:
        lines.append(
            f"Скважин с сигналом станции без ремонта: {report.wells_count}, "
            f"суммарный недобор нефти {num(report.total_oil_loss)} т/сут.",
        )
        lines.append("Наибольший недобор:")
        lines.extend(
            f"  {row.well_name} — {num(row.oil_loss or 0)} т/сут, "
            f"без ремонта {row.idle_days} сут, {row.abai_status}"
            for row in report.rows[:_TOP_IN_BODY]
        )
        lines.append("")
        lines.append("Подробности — во вложении.")
    lines.append("")
    lines.append(
        "Письмо сформировано автоматизированной системой диагностики по данным "
        "СДМО, ТМ, ABAI.",
    )
    return "\n".join(lines)


def build_message(
    report: R2LossReportDTO,
    *,
    payload: bytes,
    recipients: list[str],
    sender: str | None = None,
) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = subject_for(report)
    message["To"] = ", ".join(recipients)
    if sender:
        message["From"] = sender
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain="prs-analytics")
    message.set_content(body_for(report), charset="utf-8")
    maintype, subtype = _XLSX_MIME
    message.add_attachment(
        payload,
        maintype=maintype,
        subtype=subtype,
        filename=attachment_name(report),
    )
    return message

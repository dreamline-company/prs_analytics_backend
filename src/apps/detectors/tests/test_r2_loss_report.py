"""Отчёт «Обрыв штанг»: отбор, колонки и тексты по строкам образца, xlsx, письмо."""

# ruff: noqa: DTZ001 — метки эпизодов, статусов и телеметрии наивные, как в БД

import asyncio
import re
import zipfile
from datetime import date, datetime, timedelta
from types import SimpleNamespace

from apps.detectors.dto.internal.r2_loss_report import R2LossReportDTO
from apps.detectors.models.verification import (
    VERDICT_FAILURE_CONFIRMED,
    VERDICT_FALSE_ALARM,
)
from apps.detectors.services.r2_loss_report.builder import cdng_name
from apps.detectors.services.r2_loss_report.config import CONFIG_VERSION
from apps.detectors.services.r2_loss_report.mail import (
    all_recipients,
    attachment_name,
    build_message,
    subject_for,
)
from apps.detectors.services.r2_loss_report.render import render_xlsx
from apps.detectors.services.r2_loss_report.rows import (
    AbaiStatus,
    DriveFacts,
    Measure,
    RepairSpan,
    WellFacts,
    build_row,
    category,
    chain_start,
    display_cdng,
    eligible_episodes,
    gzu_before,
    is_producing,
    liquid_after_text,
    num,
    reference_base,
    sort_rows,
    status_on,
    total_oil_loss,
)
from apps.detectors.use_cases.get_r2_loss_report import GetR2LossReportUseCase
from shared.constants.ngdu import NGDU_ORG_TYPE

REPORT_DATE = date(2026, 10, 27)
DAY_END = datetime(2026, 10, 28)


def incident(
    incident_id: int,
    well_id: int,
    opened: datetime,
    *,
    normalized: datetime | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=incident_id,
        well_id=well_id,
        opened_at=opened,
        normalized_at=normalized,
    )


def facts(**kwargs: object) -> WellFacts:
    values = {
        "incident_id": 1,
        "well_id": 1,
        "well_name": "ZHT_0281",
        "signal_at": datetime(2026, 10, 22, 13, 15),
        "cdng": "Жанаталап",
        "plan_oil": 12.92,
        "plan_liquid": 30.0,
        "plan_water_cut": 50.0,
        "in_gzu_data": True,
    }
    return WellFacts(**(values | kwargs))


# --- отбор -------------------------------------------------------------------


def test_eligible_skips_false_alarms_repairs_and_old_episodes() -> None:
    signal = datetime(2026, 10, 5, 18, 30)
    episodes = eligible_episodes(
        [
            incident(1, 10, signal),
            # ложная тревога — не в отчёт
            incident(2, 11, signal),
            # ремонт после сигнала — не в отчёт
            incident(3, 12, signal),
            # эпизод закрыт — всё равно кандидат: эпизоды R2 закрываются ложно,
            # добывает ли скважина, решает is_producing
            incident(4, 13, signal, normalized=datetime(2026, 10, 6)),
            # старше 60 суток — не в отчёт
            incident(5, 15, DAY_END - timedelta(days=61)),
            # ремонт был и закончился задолго до сигнала — не мешает
            incident(6, 16, signal),
            # сигнал в открытом ремонте — не в отчёт
            incident(7, 17, signal),
            # ремонт заведён за 3 часа до сигнала — это ремонт по нему
            incident(8, 18, datetime(2026, 10, 22, 12, 0)),
            incident(9, 10, datetime(2026, 9, 10)),
        ],
        verdicts={1: VERDICT_FAILURE_CONFIRMED, 2: VERDICT_FALSE_ALARM},
        repairs={
            12: [RepairSpan(datetime(2026, 10, 26, 9, 0), None)],
            16: [RepairSpan(datetime(2026, 9, 1, 9, 0), datetime(2026, 9, 3))],
            17: [RepairSpan(datetime(2026, 10, 2, 9, 0), datetime(2026, 10, 9))],
            18: [RepairSpan(datetime(2026, 10, 22, 9, 0), None)],
        },
        day_end=DAY_END,
    )

    assert {well: [item.id for item in items] for well, items in episodes.items()} == {
        10: [9, 1],
        13: [4],
        16: [6],
    }


def test_chain_start_walks_back_while_no_normal_work_between() -> None:
    items = [
        incident(1, 10, datetime(2026, 9, 7, 3, 0)),
        incident(2, 10, datetime(2026, 9, 20, 11, 0)),
        incident(3, 10, datetime(2026, 10, 14, 6, 0)),
        incident(4, 10, datetime(2026, 10, 25, 9, 0)),
    ]

    def run(worked: set[tuple[int, int]]) -> int:
        async def worked_between(since: datetime, until: datetime) -> bool:
            ids = {item.opened_at: item.id for item in items}
            return (ids[since], ids[until]) in worked

        return asyncio.run(chain_start(items, worked_between=worked_between)).id

    # стояла всё время (VMB_1221: сигнал 07.09, R2 переоткрывал эпизоды)
    assert run(set()) == 1
    # между 2 и 3 скважина работала — старый эпизод не тот
    assert run({(2, 3)}) == 3
    # нормальная работа прямо перед последним эпизодом
    assert run({(3, 4)}) == 4
    assert (
        asyncio.run(
            chain_start(items[:1], worked_between=lambda *_: None),  # type: ignore[arg-type]
        ).id
        == 1
    )


def test_reference_base_is_the_highest_episode_norm() -> None:
    # у переоткрытых эпизодов сутки до сигнала — уже сломанная скважина
    assert reference_base([221.0, 38.0, None, 41.5]) == 221.0
    assert reference_base([None, 0.0]) is None


def test_producing_decided_by_drive_and_moment_at_day_end() -> None:
    working = AbaiStatus("WRK", "В работе", None, datetime(2026, 1, 1))
    idle = AbaiStatus("DWN", "В простое", "Нет подачи", datetime(2026, 10, 22))

    def drive(running: int, moment: float | None, *, data: bool = True) -> DriveFacts:
        return DriveFacts(
            base_moment=38.0,
            reference_moment=221.0,
            has_end_data=data,
            end_running=running,
            moment_end=moment,
        )

    # крутится с моментом у нормы цепочки — добывает, даже при простое в ABAI;
    # и если остановилась в последние часы после нормальной работы (отключение)
    assert is_producing(facts(status=idle, drive=drive(120, 200.0)))
    assert is_producing(facts(status=working, drive=drive(40, 190.0)))
    # ZHT_0281: вал крутится вхолостую
    assert not is_producing(facts(status=working, drive=drive(120, 31.0)))
    # VMB_1221: момент «у порога» (55 % нормы) — не добыча
    assert not is_producing(facts(status=idle, drive=drive(120, 0.55 * 221)))
    # стоит или короткий пуск
    assert not is_producing(facts(status=working, drive=drive(0, None)))
    assert not is_producing(facts(status=working, drive=drive(5, 210.0)))
    # станция молчит — решает ABAI
    assert not is_producing(facts(status=idle, drive=drive(0, None, data=False)))
    assert is_producing(facts(status=working, drive=drive(0, None, data=False)))


def test_status_on_joins_adjacent_idle_intervals() -> None:
    far = datetime(3333, 12, 31)
    intervals = [
        (datetime(2026, 1, 1), datetime(2026, 10, 21, 8, 40), "WRK", "В работе", None),
        (
            datetime(2026, 10, 21, 8, 40),
            datetime(2026, 10, 23, 10, 0),
            "DWN",
            "В простое",
            "Нет подачи",
        ),
        (datetime(2026, 10, 23, 10, 0), far, "DWN", "В простое", "ОПРС"),
    ]
    moment = DAY_END - timedelta(microseconds=1)

    status = status_on(intervals, moment=moment)

    assert status is not None
    assert status.reason == "ОПРС"
    assert status.since == datetime(2026, 10, 21, 8, 40)
    working = status_on(intervals[:1], moment=datetime(2026, 5, 1))
    assert working is not None
    assert working.since == datetime(2026, 1, 1)
    assert status_on(intervals[:1], moment=moment) is None


def test_report_is_final_only_after_morning_loads() -> None:
    def stored(built: datetime, **kwargs: object) -> SimpleNamespace:
        values = {
            "report_date": REPORT_DATE,
            "status": "completed",
            "file_id": 1,
            "config_version": CONFIG_VERSION,
            "built_at": built,
        }
        return SimpleNamespace(**(values | kwargs))

    reusable = GetR2LossReportUseCase.is_reusable
    assert reusable(stored(datetime(2026, 10, 28, 7, 35)))
    # собран в тот же день или ночью до загрузок — пересобрать
    assert not reusable(stored(datetime(2026, 10, 27, 10, 0)))
    assert not reusable(stored(datetime(2026, 10, 28, 1, 0)))
    assert not reusable(stored(datetime(2026, 10, 28, 8, 0), status="failed"))
    assert not reusable(stored(datetime(2026, 10, 28, 8, 0), config_version="old"))


# --- колонки -----------------------------------------------------------------


def test_category_uses_sample_bounds() -> None:
    assert category(4.41) == "высокодебитная"
    assert category(3.63) == "среднедебитная"
    assert category(2.04) == "среднедебитная"
    assert category(1.33) == "малодебитная"
    assert category(None) is None


def test_num_is_russian_and_trims_zeros() -> None:
    assert num(32.5) == "32,5"
    assert num(14.8) == "14,8"
    assert num(0.0) == "0"
    assert num(2.31, 2) == "2,31"
    assert num(232.4, 0) == "232"


def test_spinning_without_load_while_abai_says_working() -> None:
    """ZHT_0281: вал крутится вхолостую, в ABAI «В работе», замера после нет."""
    row = build_row(
        facts(
            last_before=Measure(datetime(2026, 10, 19, 8, 0), 0.0, 0.0),
            status=AbaiStatus("WRK", "В работе", None, datetime(2026, 1, 1)),
            drive=DriveFacts(
                base_moment=232.0,
                moment_after=26.0,
                rpm_after=110.0,
                running_at_end=True,
            ),
        ),
        report_date=REPORT_DATE,
    )

    assert row.category == "высокодебитная"
    assert row.oil_loss == 12.9
    assert row.liquid_loss_share == 1.0
    assert row.abai_status == "В работе (статус не соответствует)"
    assert row.idle_days == 5
    assert row.liquid_after == (
        "замера после сигнала нет; последний до сигнала 0 (19.10 08:00)"
    )
    assert row.what_happens.startswith(
        "Станция: 22.10 13:15 нагрузка упала с 232 до 26 (11 %) при неизменных "
        "110 об/мин и держится 5 сут — привод вращается без нагрузки.",
    )
    assert "По ABAI «В работе», ремонта нет." in row.what_happens


def test_idle_reason_not_matching_the_picture() -> None:
    """VMB_2051: «Нет жидкости» с 06.10 — причина не соответствует картине."""
    row = build_row(
        facts(
            well_name="VMB_2051",
            signal_at=datetime(2026, 10, 5, 18, 30),
            plan_oil=2.06,
            measures_after=[
                Measure(datetime(2026, 10, 6, 10, 0), 0.0, 0.0),
                Measure(datetime(2026, 10, 6, 16, 0), 0.0, 0.0),
            ],
            status=AbaiStatus(
                "DWN",
                "В простое",
                "Нет жидкости",
                datetime(2026, 10, 6, 10, 45),
            ),
            drive=DriveFacts(
                base_moment=145.0,
                moment_after=14.0,
                rpm_after=115.0,
                stopped_at=datetime(2026, 10, 6, 10, 45),
            ),
        ),
        report_date=REPORT_DATE,
    )

    assert row.category == "среднедебитная"
    assert row.abai_status == "В простое / Нет жидкости"
    assert row.idle_days == 21
    assert row.liquid_after == "0 (06.10, 2 замера)"
    assert "привод вращался без нагрузки 16 ч, затем остановлен" in row.what_happens
    assert (
        "В ABAI «Нет жидкости» с 06.10 10:45 — причина не соответствует картине, "
        "ремонта нет 21 сут." in row.what_happens
    )


def test_idle_counted_from_drive_stop_and_untypical_picture() -> None:
    """VMB_2767: в ABAI «В работе», станция встала 26.10, момент 40 % нормы."""
    row = build_row(
        facts(
            well_name="VMB_2767",
            signal_at=datetime(2026, 10, 25, 0, 15),
            status=AbaiStatus("WRK", "В работе", None, datetime(2026, 1, 1)),
            drive=DriveFacts(
                base_moment=100.0,
                moment_after=40.0,
                rpm_after=90.0,
                stopped_at=datetime(2026, 10, 25, 3, 0),
                running_at_end=False,
                last_running_at=datetime(2026, 10, 26, 8, 58),
            ),
        ),
        report_date=REPORT_DATE,
    )

    assert row.idle_days == 1
    assert "Картина не типична для обрыва (40 %, а не 10–25)." in row.what_happens


def test_idle_while_drive_keeps_running() -> None:
    """VMB_1221: «Нет подачи» с 10.09, а привод работает."""
    row = build_row(
        facts(
            well_name="VMB_1221",
            signal_at=datetime(2026, 9, 7, 9, 0),
            plan_oil=0.79,
            status=AbaiStatus(
                "DWN",
                "В простое",
                "Нет подачи",
                datetime(2026, 9, 10, 8, 0),
            ),
            drive=DriveFacts(running_at_end=True),
        ),
        report_date=REPORT_DATE,
    )

    assert row.abai_status == "В простое / Нет подачи (привод работает)"
    assert row.idle_days == 47
    assert row.category == "малодебитная"
    assert "причина не соответствует" not in row.what_happens


def test_well_missing_from_gzu_data() -> None:
    well = facts(in_gzu_data=False)

    assert liquid_after_text(well) == "скважины нет в данных ГЗУ"
    assert gzu_before(well) == (
        "скважины нет в данных ГЗУ",
        "скважины нет в данных ГЗУ",
    )


def test_gzu_before_averages_and_broken_oil_channel() -> None:
    signal = datetime(2026, 10, 21, 2, 55)
    ok = facts(
        signal_at=signal,
        measures_before=[
            Measure(signal - timedelta(days=5), 40.0, 7.0),
            Measure(signal - timedelta(days=2), 30.0, 9.0),
        ],
    )
    broken = facts(
        signal_at=signal,
        measures_before=[
            Measure(signal - timedelta(days=d), 30.6, 0.0) for d in (1, 2)
        ],
    )

    water_cut, oil = gzu_before(ok)
    assert oil == 8.0
    assert water_cut == round(((40 - 7) / 40 * 100 + (30 - 9) / 30 * 100) / 2)
    assert gzu_before(broken) == (
        "0 по всем 2 замерам при жидкости 30,6 — канал нефти/обводнённости ГЗУ "
        "не работает",
        "0 (канал не работает)",
    )
    assert gzu_before(facts()) == ("нет замеров", "нет замеров")


def test_liquid_after_lists_different_values() -> None:
    well = facts(
        measures_after=[
            Measure(datetime(2026, 9, 5, 10, 0), 2.3, 0.5),
            Measure(datetime(2026, 9, 8, 10, 0), 0.0, 0.0),
        ],
    )

    assert liquid_after_text(well) == "2,3 (05.09); 0 (08.09)"


def test_rows_sorted_by_oil_loss_and_total() -> None:
    rows = [
        build_row(facts(well_name=name, plan_oil=oil), report_date=REPORT_DATE)
        for name, oil in (("A", 0.45), ("B", 12.92), ("C", None), ("D", 6.45))
    ]

    ordered = sort_rows(rows)

    assert [row.well_name for row in ordered] == ["B", "D", "A", "C"]
    assert total_oil_loss(ordered) == 19.9


def test_cdng_shown_without_prefix_unless_numbered() -> None:
    assert display_cdng("ЦДНГ-В.Молдабек") == "В.Молдабек"
    assert display_cdng("ЦДНГ-УАЗ") == "УАЗ"
    assert display_cdng("ЦДНГ УАЗ Восточный") == "УАЗ Восточный"
    assert display_cdng("ЦДНГ-3") == "ЦДНГ-3"
    assert display_cdng(None) is None


def test_cdng_is_the_org_right_below_ngdu() -> None:
    orgs = {
        1: SimpleNamespace(
            abai_id=1,
            parent_id=None,
            org_type_id=NGDU_ORG_TYPE,
            name_ru="НГДУ Кайнармунайгаз",
            name_ru_short=None,
        ),
        2: SimpleNamespace(
            abai_id=2,
            parent_id=1,
            org_type_id=20,
            name_ru="ЦДНГ В.Молдабек",
            name_ru_short="В.Молдабек",
        ),
        3: SimpleNamespace(
            abai_id=3,
            parent_id=2,
            org_type_id=30,
            name_ru="ГУ-5",
            name_ru_short=None,
        ),
    }

    assert cdng_name(3, orgs) == "В.Молдабек"
    assert cdng_name(2, orgs) == "В.Молдабек"
    assert cdng_name(1, orgs) is None
    assert cdng_name(None, orgs) is None


# --- xlsx и письмо -----------------------------------------------------------


def report() -> R2LossReportDTO:
    rows = sort_rows(
        [
            build_row(facts(), report_date=REPORT_DATE),
            build_row(
                facts(well_name="VMB_0622", plan_oil=0.45, cdng="В.Молдабек"),
                report_date=REPORT_DATE,
            ),
        ],
    )
    return R2LossReportDTO(
        report_date=REPORT_DATE,
        config_version="test",
        wells_count=len(rows),
        total_oil_loss=total_oil_loss(rows),
        rows=rows,
    )


def sheet_xml(data: R2LossReportDTO) -> str:
    with zipfile.ZipFile(render_xlsx(data)) as archive:
        assert "xl/styles.xml" in archive.namelist()
        return archive.read("xl/worksheets/sheet1.xml").decode()


def test_xlsx_layout_matches_sample() -> None:
    xml = sheet_xml(report())

    assert '<mergeCell ref="A1:A2"/><mergeCell ref="B1:B2"/>' in xml
    assert "Суммарный недобор нефти, т/сут" in xml
    assert '<c r="B1" s="2"><v>13.4</v></c>' in xml
    assert '<row r="4"' in xml
    assert "Статус по ABAI на 27.10" in xml
    # Данные с 5-й строки: недобор жидкости в процентах, нефти — жирным 0.0,
    # дата сигнала — числом Excel.
    assert '<c r="H5" s="5"><v>1.0</v></c>' in xml
    assert '<c r="I5" s="6"><v>12.9</v></c>' in xml
    assert '<c r="M5" s="7"><v>46317</v></c>' in xml
    assert re.search(r'<c r="B6" s="4" t="inlineStr"><is><t[^>]*>VMB_0622</t>', xml)


def test_xlsx_escapes_text() -> None:
    data = report()
    data.rows[0].what_happens = "a < b & c"

    assert "a &lt; b &amp; c" in sheet_xml(data)


def test_mail_goes_to_everyone_once() -> None:
    assert all_recipients(
        ["a@x", "b@x"],
        {11: ["b@x", "c@x"], 12: ["d@x"]},
    ) == ["a@x", "b@x", "c@x", "d@x"]


def test_mail_message() -> None:
    data = report()

    message = build_message(data, payload=b"xlsx", recipients=["a@x"], sender="s@x")

    assert subject_for(data) == "Обрыв штанг на 27.10.2026: 2 скв., недобор 13,4 т/сут"
    assert message["To"] == "a@x"
    attachment = next(message.iter_attachments())
    assert attachment.get_filename() == attachment_name(data)
    assert attachment.get_content_type() == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert "ZHT_0281 — 12,9 т/сут" in message.get_body().get_content()

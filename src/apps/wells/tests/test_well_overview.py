"""Сводка по скважине: строки и наблюдения на данных VMB_1252 (29.10.2026)."""

# Времена в тестах — местные наивные, как в данных.
# ruff: noqa: DTZ001

from datetime import date, datetime, timedelta

from apps.wells.dto.internal.well_state import WellStateDTO
from apps.wells.services.well_overview.rules import (
    DonorFact,
    DriveDay,
    DynamogramFact,
    IncidentFact,
    Measurement,
    OverviewInputs,
    RepairFact,
    build_overview,
)

NOW = datetime(2026, 10, 29, 11, 57)

# Замеры ЦИТС VMB_1252 за 60 суток: (дата, жидкость, нефть). Выбросы — 18.10,
# 12.10, 02.10, 21.09, 01.09; дубли — 25.10, 25.09, 01.09.
MEASUREMENTS = [
    ("2026-10-27", 27.42, 5.16),
    ("2026-10-25", 27.72, 5.30),
    ("2026-10-25", 27.72, 5.30),
    ("2026-10-23", 25.85, 6.23),
    ("2026-10-18", 53.16, 11.27),
    ("2026-10-15", 26.96, 4.99),
    ("2026-10-12", 35.94, 15.71),
    ("2026-10-09", 26.81, 4.58),
    ("2026-10-05", 27.89, 5.50),
    ("2026-10-02", 24.10, 10.60),
    ("2026-09-29", 27.31, 4.56),
    ("2026-09-25", 28.48, 5.83),
    ("2026-09-25", 28.48, 5.83),
    ("2026-09-22", 28.74, 6.96),
    ("2026-09-21", 20.14, 11.28),
    ("2026-09-18", 25.65, 6.91),
    ("2026-09-15", 25.42, 6.82),
    ("2026-09-12", 24.75, 6.72),
    ("2026-09-08", 25.97, 7.59),
    ("2026-09-04", 25.40, 7.97),
    ("2026-09-01", 34.86, 24.50),
]
# Медианы момента по суткам 15.10–28.10 (полные сутки до «сегодня»).
MOMENTS = [69, 71, 71, 72, 71.4, 71.8, 71.7, 72, 73, 73, 73, 73, 72, 71]

REPAIRS = [
    ("2020-07-07", "2020-07-11", "Промывка песчаной пробки"),
    ("2021-01-01", "2021-01-06", "Промывка песчаной пробки"),
    ("2021-05-19", "2021-05-24", "Замена винтовой пары"),
    ("2022-05-29", "2022-05-29", "Замена привода"),
    ("2022-07-13", "2022-07-16", "Промывка песчаной пробки"),
    ("2023-04-24", "2023-04-27", "Промывка песчаной пробки"),
    ("2025-01-15", "2025-01-19", "TP 4-2 Устранение обрыва штанг"),
    ("2026-05-02T17:20", "2026-05-07T10:05", "TP 8-7 Промывка забоя"),
]


def _state(code: str, title: str, reason: str) -> WellStateDTO:
    return WellStateDTO(
        code=code,
        title_ru=title,
        reason_ru=reason,
        since=datetime(2026, 10, 15, 12, 37),
        source="sdmo",
    )


WORKING = _state("working", "Работает", "Работает в штатном режиме")


def _measurements() -> list[Measurement]:
    return [
        Measurement(
            time=datetime.fromisoformat(day) + timedelta(hours=3),
            oil=oil,
            liquid=liquid,
        )
        for day, liquid, oil in MEASUREMENTS
    ]


def _repairs() -> list[RepairFact]:
    return [
        RepairFact(datetime.fromisoformat(start), datetime.fromisoformat(end), name)
        for start, end, name in REPAIRS
    ]


def _drive_days(start: date, moments: list[float]) -> list[DriveDay]:
    return [
        DriveDay(day=start + timedelta(days=i), moment=moment)
        for i, moment in enumerate(moments)
    ]


def _vmb_1252(**overrides: object) -> OverviewInputs:
    values = {
        "now": NOW,
        "state": WORKING,
        "drive_running": True,
        "drive_since": datetime(2026, 9, 28, 16, 59, 36),  # граница поиска
        "speed": 160.0,
        "speed_units": "об/мин",
        "drive_days": _drive_days(date(2026, 10, 15), MOMENTS),
        "plan_oil": 2.27,
        "plan_liquid": 25,
        "measurements": _measurements(),
        "dynamic_level": (46.0, date(2026, 10, 27)),
        "repairs": _repairs(),
        "donor": DonorFact(
            speed=140,
            step_percent=7,
            gain=0.34,
            risk="high",
            loss_well_name="VMB_2051",
            pair_status="pending",
        ),
    }
    values.update(overrides)
    return OverviewInputs(**values)


def _items(inputs: OverviewInputs) -> dict[str, tuple[str, str]]:
    items, _ = build_overview(inputs)
    return {item.code: (item.text_ru, item.mark) for item in items}


def _observations(inputs: OverviewInputs) -> dict[str, str]:
    _, observations = build_overview(inputs)
    return {item.code: item.text_ru for item in observations}


def test_vmb_1252_items() -> None:
    items = _items(_vmb_1252())
    assert items["work"] == (
        "Привод работает без остановок не меньше 30 суток; 160 об/мин",
        "ok",
    )
    assert items["production"] == (
        "Нефть 5,2 т/сут при плане 2,3; жидкость 27,4 м³/сут при плане 25; "
        "обводнённость 81 %. Замер 27.10",
        "ok",
    )
    assert items["production_trend"] == (
        "Нефть 5,5 т/сут против 6,9 месяцем раньше (−21 %); жидкость 27,4 против "
        "25,8 (+6 %); обводнённость 73 % → 80 % (медианы замеров)",
        "attention",
    )
    # Неделя к неделе +2 % — меньше 3 %: «без заметных изменений».
    assert items["drive"] == ("Момент 69–73 за 14 суток, без заметных изменений", "ok")
    assert items["gdis"] == ("Динамический уровень 46 м на 27.10", "info")
    assert items["repairs"][0] == (
        "175 сут без ремонта; прошлый межремонтный период — 468 сут. Последний "
        "ремонт — TP 8-7 Промывка забоя, 02.05–07.05.2026"
    )
    assert items["detectors"] == ("Эпизодов за 90 суток нет", "ok")
    assert items["compensation"] == (
        "Донор для VMB_2051 (на согласовании): разгон на 7 % даст +0,34 т/сут "
        "нефти, риск высокий",
        "attention",
    )
    assert "dynamogram" not in items  # у ЭВН динамограмм нет


def test_vmb_1252_observations() -> None:
    observations = _observations(_vmb_1252())
    assert set(observations) == {
        "water_cut_rising",
        "oil_falling",
        "plan_outdated",
        "frequent_repair",
        "donor_pool_stale",
    }
    assert observations["water_cut_rising"] == (
        "Растёт обводнённость: за месяц с 73 до 80 %"
    )
    assert observations["frequent_repair"] == (
        "Частый ремонт: «Промывка песчаной пробки» — 4 из 8 последних"
    )
    assert observations["donor_pool_stale"] == (
        "В пуле доноров скорость привода 140 об/мин, фактически 160 — "
        "рекомендацию разгона нужно пересчитать"
    )


def test_stale_measurement_and_moment_rise() -> None:
    inputs = _vmb_1252(
        now=datetime(2026, 11, 10, 10, 0),
        drive_days=_drive_days(date(2026, 10, 27), [70] * 7 + [80] * 7),
    )
    items = _items(inputs)
    observations = _observations(inputs)
    assert items["production"][1] == "attention"
    assert "measurement_stale" in observations
    assert items["drive"] == (
        "Момент 70–80 за 14 суток, неделя к неделе +14 %",
        "attention",
    )
    assert observations["moment_rising"] == "Момент растёт: +14 % за неделю (70 → 80)"


def test_drive_needs_three_working_days_each_week() -> None:
    # Первая неделя — стоянка: таких суток в выборке нет (их отсекает сборщик).
    inputs = _vmb_1252(drive_days=_drive_days(date(2026, 10, 22), [72] * 7))
    assert "drive" not in _items(inputs)


def test_repair_state_without_sdmo_and_open_repair() -> None:
    inputs = OverviewInputs(
        now=NOW,
        state=_state("repair", "Ремонт", "Ремонт: Смена насоса"),
        repairs=[RepairFact(datetime(2026, 10, 27, 9, 0), None, "Смена насоса")],
    )
    items = _items(inputs)
    assert items["work"] == ("Ремонт: Смена насоса", "attention")
    assert items["production"] == ("Замеров ЦИТС за 60 суток нет", "attention")
    assert items["repairs"] == ("Идёт ремонт: Смена насоса, с 27.10", "attention")
    assert "production_trend" not in items
    assert "drive" not in items


def test_hanging_open_repair_on_working_well() -> None:
    inputs = _vmb_1252(
        repairs=[
            *_repairs(),
            RepairFact(datetime(2024, 1, 14, 17, 40), None, "Промывка"),
        ],
    )
    # Висящая запись 2024 года в середине истории не ломает текущий межремонтный.
    assert _items(inputs)["repairs"] == _items(_vmb_1252())["repairs"]
    hanging = _vmb_1252(
        repairs=[RepairFact(datetime(2024, 1, 14, 17, 40), None, "Промывка")],
    )
    assert _items(hanging)["repairs"] == (
        "Ремонт «Промывка» с 14.01.2024 не закрыт в ABAI",
        "info",
    )


def test_split_repair_records_are_one_repair() -> None:
    inputs = _vmb_1252(
        repairs=[
            RepairFact(datetime(2026, 3, 1), datetime(2026, 3, 5), "Смена насоса"),
            RepairFact(datetime(2026, 3, 4), datetime(2026, 3, 8), "Смена насоса"),
            RepairFact(datetime(2026, 3, 8, 12), datetime(2026, 3, 9), "Смена насоса"),
        ],
    )
    text, _ = _items(inputs)["repairs"]
    assert text.startswith("234 сут без ремонта. Последний ремонт")
    assert "−" not in text
    assert "frequent_repair" not in _observations(inputs)


def test_no_link_well_does_not_claim_running_drive() -> None:
    inputs = _vmb_1252(
        state=_state("no_link", "Нет связи", "Нет данных СДМО с 25.10 08:40"),
        drive_running=None,
    )
    items = _items(inputs)
    assert items["work"] == ("Нет данных СДМО с 25.10 08:40", "attention")
    assert items["compensation"] == (
        "В пуле доноров, но сейчас не может быть донором: нет связи",
        "info",
    )
    assert "donor_pool_stale" not in _observations(inputs)


def test_applied_pair_compares_with_target_speed() -> None:
    inputs = _vmb_1252(
        donor=DonorFact(
            speed=140,
            step_percent=15,
            gain=0.5,
            risk="low",
            loss_well_name="VMB_2051",
            pair_status="applied",
            speed_to=161,
        ),
    )
    assert "donor_pool_stale" not in _observations(inputs)


def test_fractional_shgn_speed_is_not_rounded_away() -> None:
    inputs = _vmb_1252(
        speed=4.9,
        speed_units="кач/мин",
        donor=DonorFact(speed=4.6, step_percent=10, gain=0.2, risk="medium"),
    )
    assert _observations(inputs)["donor_pool_stale"] == (
        "В пуле доноров скорость привода 4,6 кач/мин, фактически 4,9 — "
        "рекомендацию разгона нужно пересчитать"
    )


def test_unscaled_speed_skips_donor_check() -> None:
    assert "donor_pool_stale" not in _observations(_vmb_1252(speed=None))


def test_trend_needs_three_measurements_per_window() -> None:
    inputs = _vmb_1252(measurements=_measurements()[:4])
    assert "production_trend" not in _items(inputs)


def test_zero_plan_and_zero_previous_oil() -> None:
    measurements = [
        Measurement(NOW - timedelta(days=day), oil=oil, liquid=20.0)
        for day, oil in ((1, 1.0), (5, 1.0), (9, 1.0), (31, 0.0), (35, 0.0), (40, 0.0))
    ]
    inputs = _vmb_1252(measurements=measurements, plan_oil=0)
    items = _items(inputs)
    assert items["production"][0].startswith("Нефть 1 т/сут; жидкость")
    assert "(" not in items["production_trend"][0].split(";")[0]  # без процента
    assert "plan_outdated" not in _observations(inputs)


def test_dynamogram_and_closed_incidents() -> None:
    inputs = _vmb_1252(
        dynamogram=DynamogramFact(
            time=datetime(2026, 10, 21, 10, 0),
            condition="Влияние газа",
            severity="warning",
            fill_pct=55,
        ),
        incidents=[
            IncidentFact(
                title="Обрыв штанги",
                level="alarm",
                opened_at=datetime(2026, 9, 1),
                active=False,
                verdict="false_alarm",
            ),
            IncidentFact(
                title="Обрыв штанги",
                level="warning",
                opened_at=datetime(2026, 10, 1),
                active=False,
                verdict=None,
            ),
        ],
    )
    items = _items(inputs)
    assert items["dynamogram"] == (
        "Последний ИИ-разбор: Влияние газа, наполнение ~55 %, 21.10",
        "attention",
    )
    assert items["detectors"] == (
        "За 90 суток эпизодов: 2, все закрыты; ложных по проверке: 1",
        "info",
    )


def test_old_dates_carry_year() -> None:
    inputs = _vmb_1252(dynamic_level=(120.0, date(2025, 3, 1)))
    assert _items(inputs)["gdis"][0] == "Динамический уровень 120 м на 01.03.2025"

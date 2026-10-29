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
MOMENTS = [69, 71, 71, 72, 71.4, 71.8, 71.7, 72, 73, 73, 73, 73, 72, 71, 73]

WORKING = WellStateDTO(
    code="working",
    title_ru="Работает",
    reason_ru="Работает в штатном режиме",
    since=datetime(2026, 10, 15, 12, 37),
    source="sdmo",
)


def _measurements() -> list[Measurement]:
    return [
        Measurement(
            time=datetime.fromisoformat(day) + timedelta(hours=3),
            oil=oil,
            liquid=liquid,
        )
        for day, liquid, oil in MEASUREMENTS
    ]


def _vmb_1252(**overrides: object) -> OverviewInputs:
    values = {
        "now": NOW,
        "state": WORKING,
        "drive_running": True,
        "drive_since": datetime(2026, 9, 28, 16, 59, 36),  # граница поиска
        "speed_units": "об/мин",
        "drive_days": [
            DriveDay(day=date(2026, 10, 15) + timedelta(days=i), speed=160, moment=m)
            for i, m in enumerate(MOMENTS)
        ],
        "plan_oil": 2.27,
        "plan_liquid": 25,
        "measurements": _measurements(),
        "dynamic_level": (46.0, date(2026, 10, 27)),
        "repairs": [
            RepairFact(
                datetime(2020, 7, 7),
                datetime(2020, 7, 11),
                "Промывка песчаной пробки",
            ),
            RepairFact(
                datetime(2021, 1, 1),
                datetime(2021, 1, 6),
                "Промывка песчаной пробки",
            ),
            RepairFact(
                datetime(2021, 5, 19),
                datetime(2021, 5, 24),
                "Замена винтовой пары",
            ),
            RepairFact(datetime(2022, 5, 29), datetime(2022, 5, 29), "Замена привода"),
            RepairFact(
                datetime(2022, 7, 13),
                datetime(2022, 7, 16),
                "Промывка песчаной пробки",
            ),
            RepairFact(
                datetime(2023, 4, 24),
                datetime(2023, 4, 27),
                "Промывка песчаной пробки",
            ),
            RepairFact(
                datetime(2025, 1, 15),
                datetime(2025, 1, 19),
                "TP 4-2 Устранение обрыва штанг",
            ),
            RepairFact(
                datetime(2026, 5, 2, 17, 20),
                datetime(2026, 5, 7, 10, 5),
                "TP 8-7 Промывка забоя",
            ),
        ],
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
    # +2 % неделя к неделе — меньше 3 %: «без заметных изменений».
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
        drive_days=[
            DriveDay(day=date(2026, 10, 27) + timedelta(days=i), speed=160, moment=m)
            for i, m in enumerate([70] * 7 + [80] * 7)
        ],
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


def test_stopped_well_without_sdmo_and_open_repair() -> None:
    inputs = OverviewInputs(
        now=NOW,
        state=WellStateDTO(
            code="repair",
            title_ru="Ремонт",
            reason_ru="Ремонт: Смена насоса",
            since=datetime(2026, 10, 27, 9, 0),
            source="repair",
        ),
        repairs=[RepairFact(datetime(2026, 10, 27, 9, 0), None, "Смена насоса")],
    )
    items = _items(inputs)
    assert items["work"] == ("Ремонт: Ремонт: Смена насоса", "attention")
    assert items["production"] == ("Замеров ЦИТС за 60 суток нет", "attention")
    assert items["repairs"] == ("Идёт ремонт: Смена насоса, с 27.10", "attention")
    assert "production_trend" not in items
    assert "drive" not in items


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

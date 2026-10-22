"""Ремонт оборудования против ГТМ: правила откалиброваны на разметке заказчика."""

import pytest

from apps.repairs.services.repair_classifier import (
    GTM_REPAIR_TYPE_ID,
    is_gtm_repair,
    is_technical_repair,
)


def _gtm(
    *,
    type_id: int | None = None,
    name: str | None = None,
    text: str | None = None,
) -> bool:
    return is_gtm_repair(
        repair_type_id=type_id,
        repair_type_name=name,
        work_list=text,
    )


def test_type_name_with_measure_is_gtm() -> None:
    assert _gtm(name="По мероприятию")


def test_type_id_fallback_when_name_unknown() -> None:
    assert _gtm(type_id=GTM_REPAIR_TYPE_ID)


@pytest.mark.parametrize(
    "text",
    [
        "по меропр. ГТМ. Удлинение подвески. ↑ПО; з/з-540м",
        "Ввод из консервации, ↑ПО",
        "↑ПО; перфорация в интервалах 383-387м",
        "↑ПО; кислотная обработка призабойной зоны",
    ],
)
def test_explicit_gtm_text(text: str) -> None:
    assert _gtm(text=text)


@pytest.mark.parametrize(
    "text",
    [
        "нп; ↑ПО; Обрыв 34-ой штанги 22м Дсупер; з/з-м; Н-м; пром-м; см м³",
        "Заклин. Смена насоса. ↑ПО; з/з-258м",
        "↑ПО; Отворот НКТ 2 штук; Спуск овершот",
        "замена привода на новый",
    ],
)
def test_equipment_failure_is_not_gtm(text: str) -> None:
    assert not _gtm(text=text)


def test_failure_wins_over_flush_interval() -> None:
    # «обрыв штанги … пром-435-440м» — это отказ, а не промывка-ГТМ
    text = "нп; ↑ПО; Обрыв 44-ой штанги 22мм; з/з-437м; пром-435-440м"
    assert not _gtm(text=text)


def test_flush_with_real_interval_is_gtm() -> None:
    assert _gtm(text="↑ПО; з/з-273м; с тех водой-33м³; пром-270-287м; опресовка НКТ")


def test_empty_template_flush_is_not_gtm() -> None:
    assert not _gtm(text="↑ПО; з/з-м; Н-м; пром-м; см м³")


def test_unknown_defaults_to_equipment_repair() -> None:
    assert not _gtm(text=None)
    assert not _gtm(text="")


@pytest.mark.parametrize(
    "text",
    [
        "ГТМ, Спуск СПШ на 1800м, L-1800/60.",
        "Подъем ПО. ПВР в интервале 3115-3120м.",
        "Подготовка к проведению ГИС. Полный подъем ПО. Произвести ГИС.",
    ],
)
def test_self_labelled_gtm_from_zhylyoi_texts(text: str) -> None:
    assert _gtm(text=text)


def test_routine_pump_change_is_not_gtm() -> None:
    assert not _gtm(text="Подъем ПО с насосом и спуск ПО с расточной муфтой")


@pytest.mark.parametrize(
    "name",
    [
        "TP 8-7 Промывка забоя",  # мех. примеси
        "Очистка и промывка забоя",
        "TP 9-2 Подготовка к проведению ГИС (перфорация, запись ГИС)",
        "TP 9-1 Подготовка к геолого-техническим мероприятиям (ОПИ, ГРП, освоению)",
        "КРС. ГРП",
        "КР-12 Ликвидация скважин",
        "КРС. Консервация скважины",
        "КРС. Расконсервация",
        "Ввод из консервации",
        "Освоение скважины после бурения",
        "Ввод скважин в эксплуатацию из бурения и бездействия",
    ],
)
def test_gtm_by_repair_type_name(name: str) -> None:
    assert _gtm(name=name)


@pytest.mark.parametrize(
    "name",
    [
        "TP 4-2 Устранение обрыва штанг",
        "TP 4-1 Ревизия и смена насоса",
        "TP 4-11 Прочие виды ремонтов по ШГН (УШВН)",
        "TP 2-12 УЭВН – ШГН",  # смена способа эксплуатации — обычный ремонт
        "Промывка насоса",
        "Промывка клапанов",
        "Ликвидация обрыва штанг",
        "Ликвидация МКД",
        "КР-1 Ремонтно-изоляционные работы",
    ],
)
def test_not_gtm_by_repair_type_name(name: str) -> None:
    assert not _gtm(name=name)


def test_text_does_not_override_known_repair_type() -> None:
    # Вид работ известен — текст про перфорацию не делает ремонт насоса ГТМ
    assert not _gtm(name="TP 4-1 Ревизия и смена насоса", text="перфорация, дострел")


@pytest.mark.parametrize(
    "name",
    [
        "TP 4-8 Ревизия, смена устьевого оборудования",
        "TP 4-5 Замена полированного штока",
        "Замена СУСГ",
    ],
)
def test_technical_reason_by_repair_type_name(name: str) -> None:
    assert is_technical_repair(
        repair_type_id=None,
        repair_type_name=name,
        work_list=None,
    )


@pytest.mark.parametrize(
    "name",
    [
        "TP 4-2 Устранение обрыва штанг",
        "TP 4-1 Ревизия и смена насоса",
        "TP 8-7 Промывка забоя",  # ГТМ — не «техническая причина»
        "TP 2-12 УЭВН – ШГН",
    ],
)
def test_not_technical_reason(name: str) -> None:
    assert not is_technical_repair(
        repair_type_id=None,
        repair_type_name=name,
        work_list=None,
    )

"""Досье скважины: рендер, кэш, подстановка в системный промпт агента."""

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any, Self

from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.memory import InMemorySaver

from apps.ai_assistant.ai.well_chat_assistant import dossier as dossier_module
from apps.ai_assistant.ai.well_chat_assistant.agent import (
    WellChatContext,
    get_well_agent_config,
    well_system_prompt,
)
from apps.ai_assistant.ai.well_chat_assistant.dossier import (
    DossierData,
    WellDossierProvider,
    render_dossier,
)

NOW = datetime(2026, 10, 27, 14, 0)  # noqa: DTZ001


def _passport(**overrides: object) -> SimpleNamespace:
    base = {
        "oil_rate": 3.2,
        "plan_oil_rate": 4.5,
        "oil_rate_deviation": -1.3,
        "oil_rate_deviation_percent": -28.9,
        "liquid_rate": 10.1,
        "plan_liquid_rate": 12.0,
        "water_cut": 68.0,
        "telemetry_time": NOW - timedelta(hours=8),
        "tech_regime_date": NOW.date().replace(day=1),
        "pump_moment": 210.0,
        "pump_speed": 280.0,
        "pump_speed_units": "об/мин",
        "pump_fill": 95.0,
        "sdmo_time": NOW - timedelta(minutes=3),
        "sdmo_vlt_status": 4,
        "h_din_m": None,
        "h_din_date": None,
    }
    return SimpleNamespace(**{**base, **overrides})


def _incident(**overrides: object) -> SimpleNamespace:
    base = {
        "id": 11,
        "detector_code": "R2",
        "detector_name_ru": "Обрыв штанг",
        "level": "alarm",
        "status": "active",
        "opened_at": NOW - timedelta(hours=10),
        "last_seen_at": NOW - timedelta(hours=1),
        "verification": SimpleNamespace(
            verdict="false_alarm",
            reason="moment_recovered",
        ),
    }
    return SimpleNamespace(**{**base, **overrides})


def test_render_dossier_full() -> None:
    data = DossierData(
        well_id=6086,
        well_name="VMB_0456",
        now=NOW,
        place=("Восточный Макат", "НГДУ-Кайнармунайгаз"),
        station_type=6,
        abai_status={
            "status": "В работе",
            "reason": None,
            "since": (NOW - timedelta(days=3)).isoformat(),
        },
        card=SimpleNamespace(current_repair=None, passport=_passport()),  # type: ignore[arg-type]
        incidents=[  # type: ignore[list-item]
            _incident(),
            _incident(
                id=5,
                status="normalized",
                opened_at=NOW - timedelta(days=20),
                verification=None,
            ),
        ],
        # Счётчик — отдельным запросом, он больше ленты из 20 эпизодов.
        incident_counts={"R2": (34, 21), "R9": (2, 0)},
    )

    text = render_dossier(data)

    assert "Скважина VMB_0456 (well_id 6086)" in text
    assert "Способ добычи: ЭВН." in text
    assert "Статус ABAI сейчас: В работе" in text
    assert "Ремонт сейчас не идёт." in text
    assert "нефть 3.2 т/сут при плане 4.5 т/сут, отклонение -1.3 т/сут" in text
    assert "статус станции 4 — работает" in text
    assert "проверка: ложная тревога (moment_recovered)" in text
    assert "Эпизодов за 90 суток: R2 — 34, R9 — 2; проверка признала ложными: 21." in (
        text
    )


def test_render_dossier_skips_failed_sections() -> None:
    text = render_dossier(DossierData(well_id=1, well_name="UAZ_0031", now=NOW))

    assert text.splitlines() == [
        "Скважина UAZ_0031 (well_id 1). "
        "Досье на 27.10.2026 14:00, время везде местное.",
        "Статус ABAI сейчас: нет данных.",
    ]


def test_render_dossier_empty_last_sample() -> None:
    passport = _passport(pump_moment=None, pump_speed=None, pump_fill=None)
    data = DossierData(
        well_id=1,
        well_name="VMB_0456",
        now=NOW,
        card=SimpleNamespace(current_repair=None, passport=passport),  # type: ignore[arg-type]
    )

    assert "момент и скорость в нём не заполнены" in render_dossier(data)


def test_render_dossier_unknown_station_status_has_no_code() -> None:
    data = DossierData(
        well_id=1,
        well_name="VMB_0456",
        now=NOW,
        card=SimpleNamespace(  # type: ignore[arg-type]
            current_repair=None,
            passport=_passport(sdmo_vlt_status=None),
        ),
    )

    text = render_dossier(data)

    assert "статус станции неизвестен" in text
    assert "None" not in text


def test_render_dossier_no_incidents_in_window() -> None:
    data = DossierData(well_id=1, well_name="VMB_0456", now=NOW, incident_counts={})

    assert "Эпизодов за 90 суток не было." in render_dossier(data)


def test_provider_caches_by_well(monkeypatch: Any) -> None:  # noqa: ANN401
    calls: list[int] = []

    async def fake_build(well_id: int) -> str:
        calls.append(well_id)
        return f"досье {well_id}"

    monkeypatch.setattr(dossier_module, "build_well_dossier", fake_build)
    provider = WellDossierProvider(ttl_sec=60)

    async def scenario() -> list[str | None]:
        return [await provider.get(1), await provider.get(1), await provider.get(2)]

    assert asyncio.run(scenario()) == ["досье 1", "досье 1", "досье 2"]
    assert calls == [1, 2]


def test_provider_swallows_build_errors(monkeypatch: Any) -> None:  # noqa: ANN401
    async def broken_build(_well_id: int) -> str:
        raise RuntimeError

    monkeypatch.setattr(dossier_module, "build_well_dossier", broken_build)

    assert asyncio.run(WellDossierProvider().get(1)) is None


class _RecordingModel(GenericFakeChatModel):
    seen: list[list[BaseMessage]] = []  # noqa: RUF012

    def _generate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        _RecordingModel.seen.append(list(messages))
        return super()._generate(messages, *args, **kwargs)


def test_dossier_goes_to_system_prompt_not_history() -> None:
    _RecordingModel.seen = []
    model = _RecordingModel(messages=iter([AIMessage("первый"), AIMessage("второй")]))
    agent = create_agent(
        model=model,
        tools=[],
        middleware=[well_system_prompt],
        context_schema=WellChatContext,
        checkpointer=InMemorySaver(),
    )
    config = get_well_agent_config(thread_id="t1", well_id=6086)

    async def scenario() -> dict:
        await agent.ainvoke(
            {"messages": [HumanMessage("как скважина?")]},
            config=config,
            context=WellChatContext(well_bound=True, dossier="ДОСЬЕ-1"),
        )
        return await agent.ainvoke(
            {"messages": [HumanMessage("а сейчас?")]},
            config=config,
            context=WellChatContext(well_bound=True, dossier="ДОСЬЕ-2"),
        )

    result = asyncio.run(scenario())

    first, second = _RecordingModel.seen
    assert isinstance(first[0], SystemMessage)
    assert "ДОСЬЕ-1" in first[0].content
    # На второй вопрос — свежее досье, старое в историю не попало.
    assert "ДОСЬЕ-2" in second[0].content
    assert all("ДОСЬЕ" not in str(m.content) for m in result["messages"])


def test_render_dossier_donor_line() -> None:
    data = DossierData(
        well_id=1,
        well_name="UVK_0468",
        now=NOW,
        donor={
            "status": "pending",
            "loss_well": "UVK_0441",
            "step_percent": 15,
            "gain": 3.44,
            "risk": "low",
            "unavailable": None,
        },
    )

    assert render_dossier(data).splitlines()[-1] == (
        "Донор контура компенсации: на согласовании, закреплён за UVK_0441; "
        "разгон привода на 15 % даст 3.44 т/сут нефти (риск низкий)."
    )


def test_render_dossier_unavailable_donor() -> None:
    data = DossierData(
        well_id=1,
        well_name="UVK_0468",
        now=NOW,
        donor={
            "status": "reserve",
            "loss_well": None,
            "step_percent": 10,
            "gain": 1.2,
            "risk": "medium",
            "unavailable": "стоит: идёт ремонт",
        },
    )

    assert (
        render_dossier(data)
        .splitlines()[-1]
        .endswith(
            "(риск средний). Сейчас как донор недоступен — стоит: идёт ремонт.",
        )
    )


def test_loaders_cover_every_dossier_section() -> None:
    sections = set(DossierData.__dataclass_fields__) - {"well_id", "well_name", "now"}

    assert set(dossier_module.LOADERS) == sections


class _SessionTouched(Exception):  # noqa: N818
    pass


class _ProbeSession:
    """Любое обращение к базе — маркер: до него загрузчик собрал свои use
    case и репозитории, то есть сигнатуры конструкторов совпали."""

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        def touched(*_args: object, **_kwargs: object) -> None:
            raise _SessionTouched(name)

        return touched


def test_loaders_are_wired_to_current_signatures() -> None:
    well = SimpleNamespace(id=6086, abai_id=1001, name="VMB_0456", coords_id=None)

    for name, loader in dossier_module.LOADERS.items():
        try:
            asyncio.run(loader(_ProbeSession(), well))  # type: ignore[arg-type]
        except _SessionTouched:
            pass
        except TypeError as error:
            message = f"загрузчик {name} не собирается: {error}"
            raise AssertionError(message) from error


def test_build_survives_failing_section_and_limits_parallelism(
    monkeypatch: Any,  # noqa: ANN401
) -> None:
    class _Session:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

    class _Wells:
        def __init__(self, session: object) -> None: ...

        async def get_by_id(self, id_: int) -> SimpleNamespace:
            return SimpleNamespace(id=id_, abai_id=1, name="VMB_0456")

    running = 0
    peak = 0

    async def slow(_session: object, _well: object) -> None:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1

    async def broken(_session: object, _well: object) -> None:
        raise RuntimeError

    loaders = dict.fromkeys(dossier_module.LOADERS, slow)
    loaders["place"] = broken
    monkeypatch.setattr(dossier_module, "session_makers", {"app": _Session})
    monkeypatch.setattr(dossier_module, "WellRepository", _Wells)
    monkeypatch.setattr(dossier_module, "LOADERS", loaders)

    text = asyncio.run(dossier_module.build_well_dossier(6086))

    assert text is not None
    assert text.startswith("Скважина VMB_0456 (well_id 6086).")
    assert peak <= dossier_module.PARALLEL_SECTIONS

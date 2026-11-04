from datetime import date, datetime

from pydantic import BaseModel, Field


class R2LossRowDTO(BaseModel):
    """Строка отчёта «Обрыв штанг»: колонки A–O образца в том же порядке."""

    incident_id: int
    well_id: int
    cdng: str | None = Field(default=None, description="ЦДНГ")
    well_name: str = Field(description="Скважина")
    category: str | None = Field(default=None, description="Категория дебитности")
    plan_water_cut: float | None = Field(
        default=None,
        description="Обводнённость по режиму, %",
    )
    plan_oil: float | None = Field(default=None, description="Нефть по режиму, т/сут")
    plan_liquid: float | None = Field(
        default=None,
        description="Жидкость по режиму, м³/сут",
    )
    liquid_after: str = Field(description="Жидкость по ГЗУ после сигнала, м³/сут")
    liquid_loss_share: float = Field(description="Недобор жидкости, доля (1 = 100 %)")
    oil_loss: float | None = Field(default=None, description="Недобор нефти, т/сут")
    water_cut_before: float | str | None = Field(
        default=None,
        description="Обводнённость по ГЗУ за 30 дн до сигнала, % (или пояснение)",
    )
    oil_before: float | str | None = Field(
        default=None,
        description="Нефть по ГЗУ за 30 дн до сигнала, т/сут (или пояснение)",
    )
    what_happens: str = Field(description="Что происходит")
    signal_date: date = Field(description="С какого числа")
    abai_status: str = Field(description="Статус по ABAI на дату отчёта")
    idle_days: int = Field(description="Простой без ремонта, сут")


class R2LossReportDTO(BaseModel):
    report_date: date
    built_at: datetime | None = None
    config_version: str
    wells_count: int
    total_oil_loss: float = Field(description="Суммарный недобор нефти, т/сут")
    rows: list[R2LossRowDTO] = Field(default_factory=list)
    file_id: int | None = None
    file_name: str | None = None
    download_url: str | None = Field(default=None, description="Ссылка на xlsx")
    expires_at: datetime | None = None
    rebuilt: bool = Field(default=True, description="Собран в этом запросе")

from starlette import status

from shared.errors import HttpError


class R2LossReportDateInFutureError(HttpError):
    message = "Report date is in the future."
    code = "r2_loss_report_date_in_future"
    status_code = status.HTTP_400_BAD_REQUEST


class R2LossReportDataNotReadyError(HttpError):
    """За дату нет телеметрии ни одной станции R2 — отчёт не собираем, чтобы
    пустой лист не читался как «простоев без ремонта нет»."""

    message = "No SDMO telemetry for R2 stations on this date; report not built."
    code = "r2_loss_report_data_not_ready"
    status_code = status.HTTP_409_CONFLICT

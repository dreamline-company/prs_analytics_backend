from starlette import status

from shared.errors import HttpError


class DailySheetNgduNotFoundError(HttpError):
    message = "NGDU not found or not connected to SDMO."
    code = "daily_sheet_ngdu_not_found"
    status_code = status.HTTP_404_NOT_FOUND


class DailySheetDetectorNotFoundError(HttpError):
    message = "Detector is not registered."
    code = "daily_sheet_detector_not_found"
    status_code = status.HTTP_404_NOT_FOUND


class DailySheetDateInFutureError(HttpError):
    message = "Sheet date is in the future."
    code = "daily_sheet_date_in_future"
    status_code = status.HTTP_400_BAD_REQUEST


class DailySheetDataNotReadyError(HttpError):
    """За дату нет телеметрии НГДУ — ведомость не собираем, чтобы пустая
    таблица не читалась как «отклонений нет»."""

    message = "No SDMO telemetry for the NGDU on this date; sheet not built."
    code = "daily_sheet_data_not_ready"
    status_code = status.HTTP_409_CONFLICT

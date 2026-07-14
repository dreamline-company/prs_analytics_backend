from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Literal

import httpx
from httpcore import SyncBackend

log = logging.getLogger("uto_waybill")

JsonDict = dict[str, Any]
ConnectToRule = tuple[str | None, int | None, str, int]


# =============================================================================
# CONNECT-TO SUPPORT
# =============================================================================


def parse_connect_to(
    spec: str | list[str] | dict[Any, Any] | None,
) -> list[ConnectToRule]:
    """
    Аналог curl --connect-to для httpx.

    Форматы:
      1) "uto.emg.kmg.kz:443:10.10.10.10:443"
      2) ["uto.emg.kmg.kz:443:10.10.10.10:443", "other.kz:443:10.0.0.2:443"]
      3) {("uto.emg.kmg.kz", 443): ("10.10.10.10", 443)}

    Пустой исходный host или port означает "любой", как в curl:
      ":443:10.10.10.10:443"
    """
    if not spec:
        return []

    rules: list[ConnectToRule] = []

    if isinstance(spec, dict):
        for source, target in spec.items():
            source_host, source_port = source
            target_host, target_port = target
            rules.append(
                (
                    str(source_host).lower() if source_host else None,
                    int(source_port) if source_port else None,
                    str(target_host),
                    int(target_port),
                ),
            )
        return rules

    items = [spec] if isinstance(spec, str) else list(spec)
    for item in items:
        item = item.strip()
        if not item:
            continue

        parts = item.split(":")
        if len(parts) != 4:
            raise ValueError(
                f"Неверный connect-to: {item!r}. "
                "Ожидается формат 'HOST:PORT:TARGET_HOST:TARGET_PORT'.",
            )

        source_host, source_port, target_host, target_port = parts
        rules.append(
            (
                source_host.lower() or None,
                int(source_port) if source_port else None,
                target_host,
                int(target_port),
            ),
        )

    return rules


def match_connect_to(
    rules: list[ConnectToRule],
    host: str,
    port: int,
) -> tuple[str, int]:
    """Возвращает подмененный TCP host/port либо исходный host/port."""
    normalized_host = host.lower()
    for rule_host, rule_port, target_host, target_port in rules:
        host_matches = rule_host is None or rule_host == normalized_host
        port_matches = rule_port is None or rule_port == port
        if host_matches and port_matches:
            return target_host, target_port
    return host, port


class _ConnectToBackend(SyncBackend):
    """
    Sync network backend для httpcore/httpx.

    Важно:
    - TCP-соединение открывается на target_host:target_port.
    - URL, Host header и SNI остаются оригинальными.
    - Это позволяет ходить на внутренний IP/порт, но сохранять домен в HTTPS.
    """

    def __init__(self, rules: list[ConnectToRule]) -> None:
        self._rules = rules
        self._delegate = SyncBackend()

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ):
        target_host, target_port = match_connect_to(self._rules, host, port)
        if (target_host, target_port) != (host, port):
            log.debug(
                "connect-to: %s:%s redirected to %s:%s, original SNI=%s",
                host,
                port,
                target_host,
                target_port,
                host,
            )

        return self._delegate.connect_tcp(
            target_host,
            target_port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[Any] | None = None,
    ):
        return self._delegate.connect_unix_socket(
            path,
            timeout=timeout,
            socket_options=socket_options,
        )

    def sleep(self, seconds: float) -> None:
        return self._delegate.sleep(seconds)


class ConnectToTransport(httpx.HTTPTransport):
    """httpx transport с поддержкой connect-to."""

    def __init__(self, rules: list[ConnectToRule], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if rules:
            # httpx не имеет публичного API для connect-to, поэтому используем
            # внутренний network backend httpcore.
            self._pool._network_backend = _ConnectToBackend(rules)  # type: ignore[attr-defined]


# =============================================================================
# ERRORS
# =============================================================================


class UtoClientError(Exception):
    """Базовая ошибка UTO-клиента."""


class UtoAuthError(UtoClientError):
    """Ошибка авторизации или отсутствия токена."""


class UtoApiError(UtoClientError):
    """Ошибка API UTO."""


# =============================================================================
# DTO
# =============================================================================


@dataclass(slots=True)
class UtoCredentialsDto:
    login: str
    password: str


@dataclass(slots=True)
class AuthRequestDto:
    email: str
    password: str

    def to_payload(self) -> JsonDict:
        return {"email": self.email, "password": self.password}


@dataclass(slots=True)
class AuthResponseDto:
    jw_token: str | None
    raw: JsonDict = field(default_factory=dict, repr=False)

    @classmethod
    def from_api(cls, data: JsonDict) -> AuthResponseDto:
        token = None
        inner_data = data.get("data")
        if isinstance(inner_data, dict):
            token = inner_data.get("jwToken") or inner_data.get("jwtToken")
        token = (
            token or data.get("jwToken") or data.get("jwtToken") or data.get("token")
        )
        return cls(jw_token=token, raw=data)


@dataclass(slots=True)
class StatusDto:
    id: Any | None = None
    name_kz: str | None = None
    name_ru: str | None = None
    name_en: str | None = None
    raw: JsonDict = field(default_factory=dict, repr=False)

    @classmethod
    def from_api(
        cls,
        data: JsonDict | None,
        fallback_id: Any | None = None,
    ) -> StatusDto | None:
        if not data and fallback_id is None:
            return None
        data = data or {}
        return cls(
            id=data.get("id", fallback_id),
            name_kz=data.get("nameKz"),
            name_ru=data.get("nameRu"),
            name_en=data.get("nameEn"),
            raw=data,
        )

    @property
    def display_name(self) -> str | None:
        return self.name_kz or self.name_ru or self.name_en


@dataclass(slots=True)
class VehicleClassDto:
    code: Any | None = None
    name: str | None = None

    def to_dict(self) -> JsonDict:
        return {"code": self.code, "name": self.name}


@dataclass(slots=True)
class ToroRequestDto:
    toro_request_code: str | None = None
    object_number: str | None = None
    status_description: str | None = None
    department: str | None = None
    position: str | None = None
    production_area_name: str | None = None
    technical_location_name: str | None = None
    operation_short_name: str | None = None
    type_of_operation_name: str | None = None
    sample_key: Any | None = None
    sample_key_string: str | None = None
    raw: JsonDict = field(default_factory=dict, repr=False)

    @classmethod
    def from_api(cls, data: JsonDict | None) -> ToroRequestDto | None:
        if not isinstance(data, dict) or not data:
            return None
        return cls(
            toro_request_code=data.get("toroRequestCode"),
            object_number=data.get("objectNumber"),
            status_description=data.get("statusDescription"),
            department=data.get("department"),
            position=data.get("position"),
            production_area_name=data.get("productionAreaName"),
            technical_location_name=data.get("technicalLocationName"),
            operation_short_name=data.get("operationShortName"),
            type_of_operation_name=data.get("typeOfOperationName"),
            sample_key=data.get("sampleKey"),
            sample_key_string=data.get("sampleKeyString"),
            raw=data,
        )


@dataclass(slots=True)
class WaybillToroLinkDto:
    object_number: str | None = None
    description: str | None = None
    toro_request: ToroRequestDto | None = None
    raw: JsonDict = field(default_factory=dict, repr=False)

    @classmethod
    def from_api(cls, data: JsonDict | None) -> WaybillToroLinkDto | None:
        if not isinstance(data, dict) or not data:
            return None
        return cls(
            object_number=data.get("objectNumber"),
            description=data.get("description"),
            toro_request=ToroRequestDto.from_api(data.get("toroRequest")),
            raw=data,
        )


@dataclass(slots=True)
class WaybillApiItemDto:
    id: Any | None
    status_id: Any | None
    status: StatusDto | None
    create_waybill_date: str | None
    planed_start_date: str | None
    planed_end_date: str | None
    actual_start_date: str | None
    engine_hours: Any | None
    mileage: Any | None
    transport_equipment_number: str | None
    description: str | None
    number_mark_transport: str | None
    toro_links: list[WaybillToroLinkDto] = field(default_factory=list)
    raw: JsonDict = field(default_factory=dict, repr=False)

    @classmethod
    def from_api(cls, data: JsonDict) -> WaybillApiItemDto:
        raw_links = data.get("waybillToroRequest") or []
        if not isinstance(raw_links, list):
            raw_links = []
        links = [
            link for link in (WaybillToroLinkDto.from_api(x) for x in raw_links) if link
        ]

        return cls(
            id=data.get("id"),
            status_id=data.get("statusId"),
            status=StatusDto.from_api(
                data.get("status"),
                fallback_id=data.get("statusId"),
            ),
            create_waybill_date=data.get("createWaybillDate"),
            planed_start_date=data.get("planedStartDate"),
            planed_end_date=data.get("planedEndDate"),
            actual_start_date=data.get("actualStartDate"),
            engine_hours=data.get("engineHours"),
            mileage=data.get("mileage"),
            transport_equipment_number=data.get("transportEquipmentNumber"),
            description=data.get("description"),
            number_mark_transport=data.get("numberMarkTransport"),
            toro_links=links,
            raw=data,
        )


@dataclass(slots=True)
class WaybillDbRowDto:
    request_id: Any | None
    operation_code: str | None
    operation_number: str | None
    status_id: Any | None
    status_name: str | None
    closure_status: str | None
    department: str | None
    position: str | None
    created_at: str | None
    planned_start_at: str | None
    planned_end_at: str | None
    actual_date: str | None
    engine_hours: Any | None
    mileage: Any | None
    transport_equipment_number: str | None
    company: str | None
    division: str | None
    bpl: str | None
    well_number: str | None
    work_type: str | None
    vehicle_number: str | None
    vehicle_class: VehicleClassDto | None
    raw: JsonDict = field(default_factory=dict, repr=False)

    @classmethod
    def from_api_item(cls, item: WaybillApiItemDto) -> WaybillDbRowDto:
        first_link = item.toro_links[0] if item.toro_links else None
        toro = first_link.toro_request if first_link else None

        raw_company = item.description or ""
        raw_bpl = toro.technical_location_name if toro else ""
        well_number = extract_well_number(
            raw_company=raw_company,
            raw_bpl=raw_bpl or "",
        )

        vehicle_class = None
        if toro and toro.sample_key_string:
            vehicle_class = VehicleClassDto(
                code=toro.sample_key,
                name=toro.sample_key_string,
            )

        return cls(
            request_id=item.id,
            operation_code=toro.toro_request_code if toro else None,
            operation_number=(toro.object_number if toro else None)
            or (first_link.object_number if first_link else None),
            status_id=item.status_id,
            status_name=item.status.display_name if item.status else None,
            closure_status=(toro.status_description if toro else None)
            or (first_link.description if first_link else None),
            department=toro.department if toro else None,
            position=toro.position if toro else None,
            created_at=item.create_waybill_date,
            planned_start_at=item.planed_start_date,
            planned_end_at=item.planed_end_date,
            actual_date=item.actual_start_date,
            engine_hours=item.engine_hours,
            mileage=item.mileage,
            transport_equipment_number=item.transport_equipment_number,
            company=raw_company,
            division=toro.production_area_name if toro else None,
            bpl=raw_bpl,
            well_number=well_number,
            work_type=(toro.operation_short_name if toro else None)
            or (toro.type_of_operation_name if toro else None),
            vehicle_number=item.number_mark_transport,
            vehicle_class=vehicle_class,
            raw=item.raw,
        )

    def to_dict(self, include_raw: bool = False) -> JsonDict:
        data: JsonDict = {
            "request_id": self.request_id,
            "operation_code": self.operation_code,
            "operation_number": self.operation_number,
            "status_id": self.status_id,
            "status_name": self.status_name,
            "closure_status": self.closure_status,
            "department": self.department,
            "position": self.position,
            "created_at": self.created_at,
            "planned_start_at": self.planned_start_at,
            "planned_end_at": self.planned_end_at,
            "actual_date": self.actual_date,
            "engine_hours": self.engine_hours,
            "mileage": self.mileage,
            "transport_equipment_number": self.transport_equipment_number,
            "company": self.company,
            "division": self.division,
            "bpl": self.bpl,
            "well_number": self.well_number,
            "work_type": self.work_type,
            "vehicle_number": self.vehicle_number,
            "vehicle_class": self.vehicle_class.to_dict()
            if self.vehicle_class
            else None,
        }
        if include_raw:
            data["raw"] = self.raw
        return data


@dataclass(slots=True)
class WaybillSearchRequestDto:
    car_number: str
    target_date: date | datetime | str
    page_number: int = 1
    page_size: int = 100
    download_factories: bool = False
    download_contractors_type: int = 0
    toro_report_type: int = 0
    contractor_id: Any | None = None
    download_contractors: bool = True
    group_planner: Any | None = None
    is_contractor: bool = False
    production_area_code: Any | None = None
    technical_location: Any | None = None
    day_range_mode: Literal["same_moment", "full_day"] = "full_day"

    def to_payload(self) -> JsonDict:
        clean_car = normalize_vehicle_number(self.car_number)
        start_date, end_date = format_api_date_range(
            self.target_date,
            self.day_range_mode,
        )

        return {
            "pageNumber": self.page_number,
            "pageSize": self.page_size,
            "downloadFactories": self.download_factories,
            "downloadContractorsType": self.download_contractors_type,
            "toroReportType": self.toro_report_type,
            "contractorId": self.contractor_id,
            "downloadContractors": self.download_contractors,
            "endDate": end_date,
            "groupPlanner": self.group_planner,
            "isContractor": self.is_contractor,
            "productionAreaCode": self.production_area_code,
            "search": clean_car,
            "startDate": start_date,
            "technicalLocation": self.technical_location,
        }


@dataclass(slots=True)
class WaybillSearchResponseDto:
    items: list[WaybillApiItemDto]
    total_count: int | None = None
    page_number: int | None = None
    page_size: int | None = None
    raw: JsonDict = field(default_factory=dict, repr=False)

    @classmethod
    def from_api(cls, data: JsonDict) -> WaybillSearchResponseDto:
        raw_items = data.get("data", [])
        if not isinstance(raw_items, list):
            raw_items = []

        total_count = (
            data.get("totalCount")
            or data.get("total")
            or data.get("recordsTotal")
            or data.get("count")
        )

        return cls(
            items=[
                WaybillApiItemDto.from_api(x) for x in raw_items if isinstance(x, dict)
            ],
            total_count=int(total_count)
            if isinstance(total_count, int | str) and str(total_count).isdigit()
            else None,
            page_number=data.get("pageNumber") or data.get("page"),
            page_size=data.get("pageSize"),
            raw=data,
        )


# =============================================================================
# HELPERS
# =============================================================================

RUS_TO_ENG_VEHICLE_CHARS = str.maketrans("АВЕКМНОРСТХ", "ABEKMHOPCTX")


def normalize_vehicle_number(value: str) -> str:
    """Убирает пробелы, переводит в upper-case и заменяет похожие кириллические буквы."""
    return value.strip().upper().translate(RUS_TO_ENG_VEHICLE_CHARS)


def parse_user_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    value = value.strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue

    raise ValueError(
        f"Неверный формат даты: {value!r}. Используйте dd.mm.yyyy или yyyy-mm-dd.",
    )


def format_api_date_range(
    value: date | datetime | str,
    mode: Literal["same_moment", "full_day"] = "full_day",
) -> tuple[str, str]:
    d = parse_user_date(value)
    if mode == "same_moment":
        moment = datetime.combine(d, time.min).strftime("%Y-%m-%dT00:00:00")
        return moment, moment

    start = datetime.combine(d, time.min).strftime("%Y-%m-%dT%H:%M:%S")
    end = datetime.combine(d, time.max.replace(microsecond=0)).strftime(
        "%Y-%m-%dT%H:%M:%S",
    )
    return start, end


def extract_well_number(raw_company: str, raw_bpl: str) -> str | None:
    """Вытаскивает номер скважины из description или technicalLocationName."""
    raw_company = raw_company or ""
    raw_bpl = raw_bpl or ""

    # Примеры: "... Скважина 123", "Скважина №123", "Скважина BLG_0177"
    company_match = re.search(
        r"Скважина\s*№?\s*([^,;\n\r]+)",
        raw_company,
        flags=re.IGNORECASE,
    )
    if company_match:
        return company_match.group(1).strip()

    # Примеры: "Скв. №123", "Скв. 123", "... Скв. № BLG_0177"
    bpl_match = re.search(r"Скв\.\s*№?\s*([^,;\n\r]+)", raw_bpl, flags=re.IGNORECASE)
    if bpl_match:
        return bpl_match.group(1).strip()

    return None


def save_rows_to_json(rows: list[WaybillDbRowDto], output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump([row.to_dict() for row in rows], file, ensure_ascii=False, indent=2)
    return path


# =============================================================================
# CLIENT
# =============================================================================


class UtoWaybillClient:
    DEFAULT_BASE_URL = "https://uto.emg.kmg.kz"
    LOGIN_PATH = "/api/Account/authenticate"
    SEARCH_PATH = "/api/v1.0/Waybill/GetAll"

    def __init__(
        self,
        login: str,
        password: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 60.0,
        max_retries: int = 2,
        connect_to: str | list[str] | dict[Any, Any] | None = None,
        verify: bool | str = True,
        debug: bool = False,
    ) -> None:
        clean_login = login.strip()
        if not clean_login or not password:
            raise UtoAuthError(
                "Не переданы обязательные параметры login и/или password.",
            )

        self.base_url = base_url.rstrip("/")
        self.credentials = UtoCredentialsDto(
            login=clean_login,
            password=password,
        )
        self.timeout = timeout
        self.max_retries = max_retries
        self.debug = debug
        self._token: str | None = None

        rules = parse_connect_to(connect_to)
        transport = ConnectToTransport(rules, verify=verify, retries=max_retries)

        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "uto-waybill-client/1.0",
            },
        )

    def __enter__(self) -> UtoWaybillClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    @property
    def is_authenticated(self) -> bool:
        return bool(self._token)

    def login(self) -> AuthResponseDto:
        request_dto = AuthRequestDto(
            email=self.credentials.login,
            password=self.credentials.password,
        )

        response = self._client.post(self.LOGIN_PATH, json=request_dto.to_payload())
        if response.status_code != 200:
            raise UtoAuthError(
                f"Ошибка авторизации: HTTP {response.status_code}. Ответ: {response.text[:500]}",
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise UtoAuthError(
                f"Авторизация вернула не JSON: {response.text[:500]}",
            ) from exc

        auth_response = AuthResponseDto.from_api(data)
        if self.debug:
            safe_data = json.loads(json.dumps(data, ensure_ascii=False))
            if isinstance(safe_data.get("data"), dict):
                safe_data["data"]["jwToken"] = (
                    "***" if safe_data["data"].get("jwToken") else None
                )
            log.debug("Auth response without token: %s", safe_data)
            log.debug("Cookies: %s", self._client.cookies)

        if not auth_response.jw_token:
            raise UtoAuthError(
                "Авторизация прошла, но jwToken не найден в ответе сервера.",
            )

        self._token = auth_response.jw_token
        self._client.headers["Authorization"] = f"Bearer {self._token}"
        return auth_response

    def ensure_login(self) -> None:
        if not self.is_authenticated:
            self.login()

    def _post_json(self, path: str, payload: JsonDict) -> JsonDict:
        self.ensure_login()

        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self._client.post(path, json=payload)
            except httpx.RequestError as exc:
                last_error = exc
                log.warning(
                    "Сетевая ошибка, попытка %s/%s: %s",
                    attempt,
                    self.max_retries,
                    exc,
                )
                continue

            if response.status_code in (401, 403):
                # На случай истекшего токена пробуем перелогиниться один раз.
                if attempt == 1:
                    self._token = None
                    self.login()
                    continue
                raise UtoAuthError(
                    f"Нет доступа: HTTP {response.status_code}. Ответ: {response.text[:500]}",
                )

            if response.status_code >= 500 and attempt < self.max_retries:
                last_error = UtoApiError(
                    f"HTTP {response.status_code}: {response.text[:500]}",
                )
                continue

            if response.status_code != 200:
                raise UtoApiError(
                    f"Ошибка API: HTTP {response.status_code}. Ответ: {response.text[:500]}",
                )

            try:
                return response.json()
            except ValueError as exc:
                raise UtoApiError(f"API вернул не JSON: {response.text[:500]}") from exc

        raise UtoApiError(
            f"Запрос не выполнен после {self.max_retries} попыток: {last_error}",
        )

    def search_waybills_raw(
        self,
        request_dto: WaybillSearchRequestDto,
    ) -> WaybillSearchResponseDto:
        data = self._post_json(self.SEARCH_PATH, request_dto.to_payload())
        return WaybillSearchResponseDto.from_api(data)

    def search_waybills(
        self,
        request_dto: WaybillSearchRequestDto,
    ) -> list[WaybillDbRowDto]:
        response = self.search_waybills_raw(request_dto)
        return [WaybillDbRowDto.from_api_item(item) for item in response.items]

    def search_all_waybills(
        self,
        *,
        car_number: str,
        target_date: date | datetime | str,
        page_size: int = 100,
        max_pages: int = 20,
        day_range_mode: Literal["same_moment", "full_day"] = "full_day",
    ) -> list[WaybillDbRowDto]:
        """
        Ищет все страницы результата.

        Если API не возвращает totalCount, клиент идет по страницам до тех пор,
        пока страница не вернется пустой или пока не достигнут max_pages.
        """
        all_rows: list[WaybillDbRowDto] = []

        for page_number in range(1, max_pages + 1):
            request_dto = WaybillSearchRequestDto(
                car_number=car_number,
                target_date=target_date,
                page_number=page_number,
                page_size=page_size,
                day_range_mode=day_range_mode,
            )
            response = self.search_waybills_raw(request_dto)
            rows = [WaybillDbRowDto.from_api_item(item) for item in response.items]

            if not rows:
                break

            all_rows.extend(rows)

            if (
                response.total_count is not None
                and len(all_rows) >= response.total_count
            ):
                break

            if len(rows) < page_size:
                break

        return all_rows


# =============================================================================
# CLI
# =============================================================================


def build_output_filename(car_number: str, target_date: str) -> str:
    safe_car = re.sub(r"[^A-Z0-9_-]+", "_", normalize_vehicle_number(car_number))
    safe_date = target_date.replace(".", "-").replace("/", "-")
    return f"test_result_{safe_car}_{safe_date}.json"

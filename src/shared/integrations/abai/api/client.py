"""
abai_client_async.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx
from httpcore import AsyncNetworkBackend
from httpcore._backends.auto import AutoBackend

log = logging.getLogger("abai")


ConnectToRule = tuple[str | None, int | None, str, int]


def parse_connect_to(spec: str | list[str] | dict | None) -> list[ConnectToRule]:
    """
    Разбирает правила connect-to в список (host, port, target_host, target_port).
    Поддерживает:
      * строку 'abai.kmg.kz:443:10.32.10.98:8443'
      * список таких строк
      * dict {('abai.kmg.kz', 443): ('10.32.10.98', 8443)}
    Пустые host/port в первой паре означают «любой» (как в curl).
    """
    if not spec:
        return []
    rules: list[ConnectToRule] = []

    if isinstance(spec, dict):
        for (h, p), (th, tp) in spec.items():
            rules.append((h or None, int(p) if p else None, th, int(tp)))
        return rules

    items = [spec] if isinstance(spec, str) else list(spec)
    for item in items:
        parts = item.split(":")
        if len(parts) != 4:
            raise ValueError(
                f"Неверный формат connect-to: {item!r}. "
                "Ожидается 'HOST:PORT:TARGET_HOST:TARGET_PORT'.",
            )
        h, p, th, tp = parts
        rules.append((h or None, int(p) if p else None, th, int(tp)))
    return rules


def match_connect_to(
    rules: list[ConnectToRule],
    host: str,
    port: int,
) -> tuple[str, int]:
    """Возвращает (target_host, target_port) для (host, port) либо исходные значения."""
    for rh, rp, th, tp in rules:
        if (rh is None or rh == host) and (rp is None or rp == port):
            return th, tp
    return host, port


class _ConnectToBackend(AsyncNetworkBackend):
    """
    Network backend, который при connect_tcp подменяет адрес назначения по
    правилам connect-to. SNI и server_hostname остаются исходными (их httpcore
    берёт из origin при start_tls), поэтому Host-заголовок и проверка
    сертификата продолжают работать для оригинального домена.
    """

    def __init__(self, rules: list[ConnectToRule]) -> None:
        self._rules = rules
        self._delegate = AutoBackend()

    async def connect_tcp(
        self,
        host,
        port,
        timeout=None,
        local_address=None,
        socket_options=None,
    ):
        target_host, target_port = match_connect_to(self._rules, host, port)
        if (target_host, target_port) != (host, port):
            log.debug(
                "connect-to: %s:%s -> %s:%s (SNI=%s)",
                host,
                port,
                target_host,
                target_port,
                host,
            )
        return await self._delegate.connect_tcp(
            target_host,
            target_port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        return await self._delegate.connect_unix_socket(
            path,
            timeout=timeout,
            socket_options=socket_options,
        )

    async def sleep(self, seconds):
        await self._delegate.sleep(seconds)


class ConnectToTransport(httpx.AsyncHTTPTransport):
    """httpx-транспорт с поддержкой connect-to (подмена TCP-адреса)."""

    def __init__(self, rules: list[ConnectToRule], **kwargs) -> None:
        super().__init__(**kwargs)
        if rules:
            self._pool._network_backend = _ConnectToBackend(rules)


class AbaiError(Exception):
    """Базовая ошибка клиента ABAI."""


class AbaiAuthError(AbaiError):
    """Ошибка аутентификации / истёкшая сессия."""


@dataclass
class AbaiFile:
    """Файл-вложение, который можно скачать через /ru/attachments/{id}."""

    id: int
    file_name: str
    file_size: str | None = None
    file_path: str | None = None
    document_id: int | None = None
    entity_id: int | None = None
    measure_date: str | None = None  # дата замера (для динамограмм ГДИС), 'дд.мм.гггг'
    raw: dict = field(default_factory=dict, repr=False)


@dataclass
class PrsRecord:
    """Одна запись ПРС/КРС (ремонт скважины)."""

    id: int
    well_id: int | None
    date_begin: str | None
    date_end: str | None
    repair_type_name: str | None
    parent_repair_type_name: str | None
    contractor: Any | None
    brigade: Any | None
    work_list: str | None
    reason_equip_fail: Any | None
    files: list[AbaiFile] = field(default_factory=list)
    raw: dict = field(default_factory=dict, repr=False)


@dataclass
class GdisMetric:
    """Одна метрика ГДИС с историей замеров по датам."""

    code: str
    name: str | None
    last_measure_date: str | None
    last_measure_value: Any | None
    measurements: dict[str, Any] = field(default_factory=dict)


@dataclass
class GdisResult:
    """Полный разобранный ответ ГДИС по скважине."""

    well_id: int
    measure_dates: list[str] = field(default_factory=list)
    metrics: list[GdisMetric] = field(default_factory=list)
    conclusion_code: int | None = None
    conclusion_name: str | None = None
    target: Any | None = None
    dynamogram_files: list[AbaiFile] = field(default_factory=list)
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def dynamogram_by_date(self) -> dict[str, list[AbaiFile]]:
        """Мапа 'дд.мм.гггг' -> список файлов динамограмм этой даты."""
        out: dict[str, list[AbaiFile]] = {}
        for f in self.dynamogram_files:
            out.setdefault(f.measure_date or "—", []).append(f)
        return out


class AbaiAsyncClient:
    BASE_URL = "https://abai.kmg.kz"
    LOGIN_PAGE = "/ru/login"
    PRELOGIN = "/ru/prelogin"

    DEFAULT_UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36"
    )

    def __init__(
        self,
        username: str | None = None,
        password: str | None = None,
        domain: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float = 60.0,
        max_retries: int = 3,
        retry_backoff: float = 1.5,
        max_concurrent_downloads: int = 5,
        connect_to: str | list[str] | dict | None = None,
        verify: bool | str = True,
    ) -> None:
        self.username = username or os.environ.get("ABAI_USERNAME", "")
        self.password = password or os.environ.get("ABAI_PASSWORD", "")
        self.domain = domain or os.environ.get("ABAI_DOMAIN", "emg_new")
        if base_url:
            self.BASE_URL = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self._download_sem = asyncio.Semaphore(max_concurrent_downloads)

        # connect-to: аналог curl --connect-to (подмена TCP-адреса при
        # сохранении Host/SNI/сертификата). По умолчанию берётся из ABAI_CONNECT_TO.
        if connect_to is None:
            env_ct = os.environ.get("ABAI_CONNECT_TO")
            connect_to = env_ct.split(",") if env_ct else None
        rules = parse_connect_to(connect_to)
        transport = ConnectToTransport(rules, verify=verify, retries=max_retries)

        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
            headers={
                "User-Agent": self.DEFAULT_UA,
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            },
        )
        self._logged_in = False

    # ---- управление жизненным циклом / контекстный менеджер -------------- #
    async def __aenter__(self) -> AbaiAsyncClient:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    # ----------------------------- низкий уровень -------------------------- #
    @property
    def _xsrf_token(self) -> str | None:
        """X-XSRF-TOKEN = URL-декодированное значение куки XSRF-TOKEN (Laravel)."""
        raw = self._client.cookies.get("XSRF-TOKEN")
        return unquote(raw) if raw else None

    def _api_headers(self, extra: dict | None = None) -> dict:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest",
        }
        token = self._xsrf_token
        if token:
            headers["X-XSRF-TOKEN"] = token
        if extra:
            headers.update(extra)
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        headers: dict | None = None,
        expect_json: bool = True,
        **kwargs,
    ) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = await self._client.request(
                    method,
                    path,
                    headers=self._api_headers(headers),
                    **kwargs,
                )
            except httpx.RequestError as exc:
                last_exc = exc
                log.warning(
                    "Сетевая ошибка %s %s (попытка %d/%d): %s",
                    method,
                    path,
                    attempt,
                    self.max_retries,
                    exc,
                )
                await asyncio.sleep(self.retry_backoff * attempt)
                continue

            if resp.status_code in (401, 419) or (
                expect_json and "login" in str(resp.url) and "/api/" in path
            ):
                raise AbaiAuthError(
                    f"Сессия недействительна (status={resp.status_code}, url={resp.url}). "
                    "Нужен повторный login().",
                )
            if resp.status_code >= 500:
                last_exc = AbaiError(f"HTTP {resp.status_code} от {path}")
                log.warning(
                    "Серверная ошибка %s (попытка %d/%d)",
                    resp.status_code,
                    attempt,
                    self.max_retries,
                )
                await asyncio.sleep(self.retry_backoff * attempt)
                continue
            return resp
        raise AbaiError(f"Не удалось выполнить {method} {path}: {last_exc}")

    async def _get_json(self, path: str, params: dict | None = None) -> Any:
        resp = await self._request("GET", path, params=params)
        resp.raise_for_status()
        try:
            return resp.json()
        except ValueError as exc:
            raise AbaiError(
                f"Ожидался JSON от {path}, получено: {resp.text[:200]!r}",
            ) from exc

    # ------------------------------- логин --------------------------------- #
    async def login(self) -> AbaiAsyncClient:
        """
        Логин и сохранение сессии:
          1. GET /ru/login   -> _token (CSRF формы) + стартовые куки.
          2. POST /ru/prelogin (form-urlencoded) -> сессионные куки.
        """
        if not self.username or not self.password:
            raise AbaiAuthError(
                "Не заданы учётные данные. Передайте username/password "
                "или задайте ABAI_USERNAME / ABAI_PASSWORD.",
            )

        log.info("Загружаю страницу логина для получения _token…")
        page = await self._request("GET", self.LOGIN_PAGE, expect_json=False)
        page.raise_for_status()
        token = self._extract_form_token(page.text)
        if not token:
            raise AbaiAuthError("Не удалось найти _token на странице логина.")

        log.info("Отправляю учётные данные на %s…", self.PRELOGIN)
        data = {
            "_token": token,
            "username": self.username,
            "domain": self.domain,
            "password": self.password,
        }
        resp = await self._request(
            "POST",
            self.PRELOGIN,
            data=data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": self.BASE_URL,
                "Referer": f"{self.BASE_URL}{self.LOGIN_PAGE}",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            expect_json=False,
        )

        if str(resp.url).rstrip("/").endswith("login"):
            raise AbaiAuthError(
                "Логин не прошёл — портал вернул страницу входа. "
                "Проверьте username/domain/password.",
            )
        if not self._client.cookies.get("kmg_ai_session"):
            raise AbaiAuthError(
                "После логина не получена сессионная кука kmg_ai_session.",
            )

        self._logged_in = True
        log.info("Логин успешен (user=%s, domain=%s).", self.username, self.domain)
        return self

    @staticmethod
    def _extract_form_token(html: str) -> str | None:
        m = re.search(r'name=["\']_token["\']\s+value=["\']([^"\']+)["\']', html)
        if m:
            return m.group(1)
        m = re.search(r'name=["\']csrf-token["\']\s+content=["\']([^"\']+)["\']', html)
        return m.group(1) if m else None

    async def _ensure_login(self) -> None:
        if not self._logged_in:
            await self.login()

    # ----------------------------- скважины -------------------------------- #
    async def search_wells(self, query: str, dzo: int = 4) -> list[dict]:
        """GET /ru/api/bigdata/wells/search -> [{'id': 118, 'name': 'BLG_0177'}, ...]."""
        await self._ensure_login()
        data = await self._get_json(
            "/ru/api/bigdata/wells/search",
            params={"query": query, "selectedUserDzo": dzo},
        )
        return data.get("items", []) if isinstance(data, dict) else []

    async def get_well_id(self, name: str, dzo: int = 4) -> int:
        """Точный поиск well_id по имени скважины."""
        items = await self.search_wells(name, dzo=dzo)
        for it in items:
            if str(it.get("name", "")).strip().lower() == name.strip().lower():
                return int(it["id"])
        if items:
            log.warning(
                "Точное совпадение '%s' не найдено, беру первое: %s",
                name,
                items[0],
            )
            return int(items[0]["id"])
        raise AbaiError(f"Скважина '{name}' не найдена (dzo={dzo}).")

    async def get_well_dzo(self, well_id: int) -> str:
        """GET /ru/api/bigdata/dict/dzo/{id} -> код ДЗО (например 'EMG')."""
        await self._ensure_login()
        resp = await self._request("GET", f"/ru/api/bigdata/dict/dzo/{well_id}")
        return resp.text.strip()

    # ----------------------------- справочники ----------------------------- #
    async def get_dict(self, name: str) -> dict[int, str]:
        """GET /ru/api/bigdata/dict/{name} -> {id: name}."""
        await self._ensure_login()
        data = await self._get_json(f"/ru/api/bigdata/dict/{name}")
        result: dict[int, str] = {}
        if isinstance(data, list):
            for row in data:
                if isinstance(row, dict) and "id" in row:
                    result[int(row["id"])] = row.get("name")
        return result

    # ================================ ПРС ================================== #
    async def get_prs_schema(self, well_id: int) -> dict:
        """GET /ru/api/bigdata/forms/prs?well_id={id} — описание формы (схема полей)."""
        await self._ensure_login()
        return await self._get_json(
            "/ru/api/bigdata/forms/prs",
            params={"well_id": well_id},
        )

    async def get_prs_results(
        self,
        well_id: int,
        type_: str = "well",
    ) -> list[PrsRecord]:
        """
        ПРС/КРС по скважине.
        GET /ru/api/bigdata/forms/prs/results?well_id={id}&type=well
        """
        await self._ensure_login()
        data = await self._get_json(
            "/ru/api/bigdata/forms/prs/results",
            params={"well_id": well_id, "type": type_},
        )
        rows = data.get("rows", []) if isinstance(data, dict) else []
        records: list[PrsRecord] = []
        for r in rows:
            records.append(
                PrsRecord(
                    id=r.get("id"),
                    well_id=r.get("well"),
                    date_begin=r.get("dbeg"),
                    date_end=r.get("dend"),
                    repair_type_name=r.get("repair_work_type_name"),
                    parent_repair_type_name=r.get("parent_repair_work_type_name"),
                    contractor=r.get("contractor"),
                    brigade=r.get("brigade"),
                    work_list=r.get("work_list"),
                    reason_equip_fail=r.get("reason_equip_fail")
                    or r.get("reason_equip_fail_name"),
                    files=self._extract_files_from_documents(r.get("documents") or []),
                    raw=r,
                ),
            )
        return records

    @staticmethod
    def _extract_files_from_documents(documents: Iterable[dict]) -> list[AbaiFile]:
        files: list[AbaiFile] = []
        for doc in documents:
            doc_id = doc.get("id")
            for f in doc.get("file") or []:
                info = f.get("info") or {}
                files.append(
                    AbaiFile(
                        id=int(f["id"]),
                        file_name=f.get("filename")
                        or info.get("file_name")
                        or f"file_{f['id']}",
                        file_size=info.get("file_size"),
                        file_path=info.get("file_path"),
                        document_id=doc_id,
                        entity_id=f.get("entity_id"),
                        raw=f,
                    ),
                )
        return files

    async def get_gdis_schema(self, well_id: int, type_: str = "well") -> dict:
        """GET /ru/api/bigdata/forms/current_g_d_i_s?id={id}&type=well — фильтры/дата по умолчанию."""
        await self._ensure_login()
        return await self._get_json(
            "/ru/api/bigdata/forms/current_g_d_i_s",
            params={"id": well_id, "type": type_},
        )

    async def get_gdis_results(
        self,
        well_id: int,
        on_date: date | datetime | str | None = None,
        type_: str = "well",
        page: int = 1,
        resolve_conclusion: bool = True,
    ) -> GdisResult:
        """
        ГДИС по скважине.
        GET /ru/api/bigdata/forms/current_g_d_i_s/results
            ?filter[date]={ISO}&id={id}&type=well&page=1
        """
        await self._ensure_login()

        if on_date is None:
            schema = await self.get_gdis_schema(well_id, type_=type_)
            on_date = self._default_date_from_schema(schema)
        filter_date = self._to_iso_z(on_date)

        params = {
            "filter[date]": filter_date,
            "id": well_id,
            "type": type_,
            "page": page,
        }
        print(params)
        data = await self._get_json(
            "/ru/api/bigdata/forms/current_g_d_i_s/results",
            params=params,
        )

        columns = data.get("columns", []) if isinstance(data, dict) else []
        rows = data.get("rows", []) if isinstance(data, dict) else []

        date_cols = [
            c["code"]
            for c in columns
            if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", str(c.get("code", "")))
        ]

        result = GdisResult(well_id=well_id, measure_dates=date_cols, raw=data)

        for row in rows:
            code = row.get("code")
            if code == "conclusion":
                result.conclusion_code = (row.get("last_measure_value") or {}).get(
                    "value",
                )
            elif code == "target":
                result.target = (row.get("last_measure_value") or {}).get("value")
            elif code == "file_dynamogram":
                result.dynamogram_files = self._extract_dynamogram_files(row)
            else:
                metric = GdisMetric(
                    code=code,
                    name=(row.get("value") or {}).get("name"),
                    last_measure_date=(row.get("last_measure_date") or {}).get("name"),
                    last_measure_value=(row.get("last_measure_value") or {}).get(
                        "value",
                    ),
                )
                for d in date_cols:
                    cell = row.get(d)
                    if isinstance(cell, dict) and "value" in cell:
                        metric.measurements[d] = cell["value"]
                result.metrics.append(metric)

        if resolve_conclusion and result.conclusion_code is not None:
            try:
                concl = await self.get_dict("gdis_conclusion")
                result.conclusion_name = concl.get(int(result.conclusion_code))
            except Exception as exc:  # noqa: BLE001
                log.debug("Не удалось расшифровать заключение: %s", exc)

        return result

    @staticmethod
    def _extract_dynamogram_files(row: dict) -> list[AbaiFile]:
        """
        Собирает файлы динамограмм с сохранением даты замера.
        В строке file_dynamogram каждая колонка-дата ('дд.мм.гггг') содержит
        свой список файлов в cell['value']. Возвращает список AbaiFile с
        проставленным measure_date, отсортированный по дате (свежие сверху).
        """
        files: list[AbaiFile] = []
        seen: set[int] = set()

        def _as_dt(k: str) -> datetime:
            try:
                return datetime.strptime(k, "%d.%m.%Y")
            except ValueError:
                return datetime.min

        date_keys = [k for k in row if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", str(k))]
        for k in sorted(date_keys, key=_as_dt, reverse=True):
            cell = row.get(k)
            val = cell.get("value") if isinstance(cell, dict) else None
            if not isinstance(val, list):
                continue
            for f in val:
                fid = f.get("id")
                if fid is None or fid in seen:
                    continue
                seen.add(fid)
                files.append(
                    AbaiFile(
                        id=int(fid),
                        file_name=f.get("file_name") or f"dynamogram_{fid}.png",
                        file_size=f.get("file_size"),
                        file_path=f.get("file_path"),
                        measure_date=k,
                        raw=f,
                    ),
                )
        return files

    @staticmethod
    def _default_date_from_schema(schema: dict) -> str:
        try:
            for flt in schema.get("params", {}).get("filter", []):
                if flt.get("code") == "date" and flt.get("default"):
                    return flt["default"]
        except Exception:  # noqa: BLE001
            pass
        return date.today().isoformat()

    @staticmethod
    def _to_iso_z(value: date | datetime | str) -> str:
        """Приводит дату к '2026-06-19T00:00:00.000Z' (как в запросах фронта)."""
        if isinstance(value, str):
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                return f"{value}T00:00:00.000Z"
            return value
        d = value.date() if isinstance(value, datetime) else value
        return f"{d.isoformat()}T00:00:00.000Z"

    # ============================ СКАЧИВАНИЕ ============================== #
    async def download_attachment(
        self,
        file_id: int,
        dest_dir: str | Path = "downloads",
        filename: str | None = None,
        chunk_size: int = 1 << 16,
    ) -> Path:
        """
        Скачивает вложение: GET /ru/attachments/{file_id} (стримингом).
        file_id — это AbaiFile.id. Ограничено семафором.
        """
        await self._ensure_login()
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)

        async with self._download_sem:
            headers = {"Accept": "*/*"}
            token = self._xsrf_token
            if token:
                headers["X-XSRF-TOKEN"] = token

            async with self._client.stream(
                "GET",
                f"/ru/attachments/{file_id}",
                headers=headers,
            ) as resp:
                if resp.status_code in (401, 419) or "login" in str(resp.url):
                    raise AbaiAuthError(f"Нет доступа к вложению {file_id} (сессия?).")
                resp.raise_for_status()

                name = (
                    filename
                    or self._filename_from_response(resp)
                    or f"attachment_{file_id}"
                )
                out_path = dest / self._safe_filename(name)
                with open(out_path, "wb") as fh:
                    async for chunk in resp.aiter_bytes(chunk_size):
                        if chunk:
                            fh.write(chunk)

        log.info("Скачано: %s (%d байт)", out_path, out_path.stat().st_size)
        return out_path

    @staticmethod
    def _filename_from_response(resp: httpx.Response) -> str | None:
        cd = resp.headers.get("Content-Disposition", "")
        m = re.search(r"filename\*=UTF-8''([^;]+)", cd)
        if m:
            return unquote(m.group(1))
        m = re.search(r'filename="?([^"]+)"?', cd)
        return m.group(1).strip() if m else None

    @staticmethod
    def _safe_filename(name: str) -> str:
        return re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "file"

    # ---- конкурентное скачивание документов ПРС / динамограмм ГДИС -------- #
    async def download_prs_documents(
        self,
        well_id: int,
        dest_dir: str | Path = "downloads",
        type_: str = "well",
    ) -> list[Path]:
        """Скачивает все документы ПРС скважины конкурентно (ограничено семафором)."""
        records = await self.get_prs_results(well_id, type_=type_)
        files = [f for rec in records for f in rec.files]
        return await self._download_many(files, dest_dir)

    async def download_gdis_dynamograms(
        self,
        well_id: int,
        dest_dir: str | Path = "downloads",
        on_date: date | datetime | str | None = None,
        type_: str = "well",
    ) -> list[Path]:
        """
        Скачивает все файлы динамограмм ГДИС конкурентно.
        Имя сохраняется как '{дата}_{id}_{имя}', чтобы одноимённые файлы
        (часто все называются '177.png') не перезаписывали друг друга.
        """
        result = await self.get_gdis_results(well_id, on_date=on_date, type_=type_)
        return await self._download_many(
            result.dynamogram_files,
            dest_dir,
            name_fn=lambda f: (
                f"{(f.measure_date or 'nodate').replace('.', '-')}_{f.id}_{f.file_name}"
            ),
        )

    async def _download_many(
        self,
        files: list[AbaiFile],
        dest_dir: str | Path,
        name_fn=None,
    ) -> list[Path]:
        """Качает список файлов параллельно; ошибки логируются, не прерывают остальные."""

        async def _one(f: AbaiFile) -> Path | None:
            try:
                name = name_fn(f) if name_fn else f.file_name
                return await self.download_attachment(
                    f.id,
                    dest_dir=dest_dir,
                    filename=name,
                )
            except Exception as exc:  # noqa: BLE001
                log.error(
                    "Не удалось скачать вложение %s (%s): %s",
                    f.id,
                    f.file_name,
                    exc,
                )
                return None

        results = await asyncio.gather(*(_one(f) for f in files))
        return [p for p in results if p is not None]

from typing import Literal
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from shared.constants.ngdu import AbaiNGDUIDsEnum

ENV_TYPE = Literal["dev", "prod", "local"]
LOG_LEVEL_TYPE = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class CoreSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )

    # APP ENV
    ENV: ENV_TYPE
    LOG_LEVEL: LOG_LEVEL_TYPE = "ERROR"


class Settings(CoreSettings):
    # APP ENV
    APP_NAME: str = "prs-analytics"

    # Time
    TZ_NAME: str = "Asia/Atyrau"

    @property
    def ZONE_INFO(self) -> ZoneInfo:  # noqa: N802
        return ZoneInfo(self.TZ_NAME)

    # app database
    APP_ASYNC_DATABASE_URL: SecretStr
    APP_SYNC_DATABASE_URL: SecretStr
    ALEMBIC_VERSION_TABLE_NAME: str = "prs_alembic_versions"

    # External Databases
    # ABAI VIEWS
    ABAI_ASYNC_DATABASE_URL: str
    # CM DB
    CM_ASYNC_DATABASE_URL: str
    # SDMO DB (MySQL) — у каждого НГДУ своя база с одинаковой схемой, ключ —
    # AbaiNGDUIDsEnum. Незаданный URL = НГДУ ещё не подключён, загрузчик и
    # beat его пропускают.
    SDMO_KMG_ASYNC_DATABASE_URL: str | None = None
    SDMO_DMG_ASYNC_DATABASE_URL: str | None = None
    SDMO_ZHMG_ASYNC_DATABASE_URL: str | None = None
    SDMO_ZHLMG_ASYNC_DATABASE_URL: str | None = None

    def sdmo_database_urls(self) -> dict[AbaiNGDUIDsEnum, str]:
        """URL SDMO-баз по НГДУ — только настроенные."""
        urls = {
            AbaiNGDUIDsEnum.KMG: self.SDMO_KMG_ASYNC_DATABASE_URL,
            AbaiNGDUIDsEnum.DMG: self.SDMO_DMG_ASYNC_DATABASE_URL,
            AbaiNGDUIDsEnum.ZHMG: self.SDMO_ZHMG_ASYNC_DATABASE_URL,
            AbaiNGDUIDsEnum.ZHlMG: self.SDMO_ZHLMG_ASYNC_DATABASE_URL,
        }
        return {ngdu: url for ngdu, url in urls.items() if url}

    # CM media
    CM_MEDIA_URL_HEADER: str = "http://188.127.32.80:8000/media"

    # WINCC
    ODBC_DRIVER: str = "ODBC Driver 18 for SQL Server"

    KAINAR_HOST: str
    KAINAR_PORT: int
    KAINAR_DATABASE: str
    KAINAR_USER: str
    KAINAR_PASSWORD: str

    DMG_HOST: str
    DMG_PORT: int
    DMG_DATABASE: str
    DMG_USER: str
    DMG_PASSWORD: str

    ZHMG_HOST: str
    ZHMG_PORT: int
    ZHMG_DATABASE: str
    ZHMG_USER: str
    ZHMG_PASSWORD: str

    ZHYLMG_HOST: str
    ZHYLMG_PORT: int
    ZHYLMG_DATABASE: str
    ZHYLMG_USER: str
    ZHYLMG_PASSWORD: str

    @classmethod
    def _get_odbc_url(  # noqa: PLR0913
        cls,
        driver: str,
        host: str,
        port: int,
        uid: str,
        password: str,
        db: str,
    ) -> str:
        odbc_str = (
            f"DRIVER={driver};"
            f"SERVER={host},{port};"
            f"DATABASE={db};"
            f"UID={uid};"
            f"PWD={password};"
            "TrustServerCertificate=yes;"
        )
        return f"mssql+aioodbc:///?odbc_connect={quote_plus(odbc_str)}"

    @property
    def DMG_TELEMETRY_ASYNC_DATABASE_URL(self) -> str:  # noqa: N802
        return self._get_odbc_url(
            self.ODBC_DRIVER,
            self.DMG_HOST,
            self.DMG_PORT,
            self.DMG_USER,
            self.DMG_PASSWORD,
            self.DMG_DATABASE,
        )

    @property
    def KAINAR_TELEMETRY_ASYNC_DATABASE_URL(self) -> str:  # noqa: N802
        return self._get_odbc_url(
            self.ODBC_DRIVER,
            self.KAINAR_HOST,
            self.KAINAR_PORT,
            self.KAINAR_USER,
            self.KAINAR_PASSWORD,
            self.KAINAR_DATABASE,
        )

    @property
    def ZHMG_TELEMETRY_ASYNC_DATABASE_URL(self) -> str:  # noqa: N802
        return self._get_odbc_url(
            self.ODBC_DRIVER,
            self.ZHMG_HOST,
            self.ZHMG_PORT,
            self.ZHMG_USER,
            self.ZHMG_PASSWORD,
            self.ZHMG_DATABASE,
        )

    @property
    def ZHYLMG_TELEMETRY_ASYNC_DATABASE_URL(self) -> str:  # noqa: N802
        return self._get_odbc_url(
            self.ODBC_DRIVER,
            self.ZHYLMG_HOST,
            self.ZHYLMG_PORT,
            self.ZHYLMG_USER,
            self.ZHYLMG_PASSWORD,
            self.ZHYLMG_DATABASE,
        )

    # ABAI WEB CLIENT
    ABAI_LOGIN: str
    ABAI_PASS: str
    ABAI_DOMAIN: str = "emg_new"
    ABAI_CONNECT_THROUGH: str = "abai.kmg.kz:443:10.32.10.98:8443"

    # UTO transport
    UTO_LOGIN: str
    UTO_PASS: str
    UTO_CONNECT_THROUGH: str | None = None

    # TOUCAN KBRS CLIENT
    KBRS_HOST: str = "10.32.10.86"
    KBRS_LOGIN: str
    KBRS_PASSWORD: str
    KBRS_POOL_SIZE: int = 10

    # Фоновый опросчик замеров КБРС (apps/kbrs/tasks/poll_measures):
    # период опроса, keepalive-пинг простаивающих сессий, скользящее окно
    # списка замеров, «живость» замера (перечитываем, пока end_time ближе
    # к now, чем grace) и размер страницы TNOMeasureList.
    KBRS_POLL_INTERVAL_SECONDS: int = 60
    KBRS_KEEPALIVE_INTERVAL_SECONDS: int = 240
    KBRS_POLL_WINDOW_HOURS: int = 24
    KBRS_POLL_REFRESH_GRACE_MINUTES: int = 120
    KBRS_POLL_PAGE_COUNT: int = 200

    # S3 (minio)
    S3_ACCESS_KEY: str
    S3_SECRET_KEY: str
    S3_ENDPOINT_URL: str
    # Host used to sign download URLs handed to the frontend. When set, the
    # backend still talks to the internal ``S3_ENDPOINT_URL`` for uploads and
    # reads, but presigned URLs point here (typically a reverse-proxied MinIO
    # exposed to the browser). Leave empty to hand out the internal URL.
    S3_PUBLIC_URL: str | None = None

    # Единый бакет приложения: документы ремонтов, динамограммы, СПО, замеры
    # КБРС. Строки ``files_file`` хранят только ключ, поэтому бакет один на
    # всех читателей и писателей. Прежнее имя переменной принимается как алиас.
    S3_BUCKET_NAME: str = Field(
        default="prs-analytics-bucket",
        validation_alias=AliasChoices("S3_BUCKET_NAME", "PRS_REPAIRS_BUCKET_NAME"),
    )

    # Redis
    REDIS_USER: str
    REDIS_USER_PASSWORD: str
    REDIS_HOST: str
    REDIS_PORT: int

    @property
    def REDIS_URL(self) -> str:  # noqa: N802
        return f"redis://{self.REDIS_USER}:{self.REDIS_USER_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}"

    @property
    def APP_REDIS_DB_URL(self) -> str:  # noqa: N802
        return self.REDIS_URL + "/0"

    @property
    def CELERY_BROKER_REDIS_DB_URL(self) -> str:  # noqa: N802
        return self.REDIS_URL + "/1"

    @property
    def CELERY_BACKEND_REDIS_DB_URL(self) -> str:  # noqa: N802
        return self.REDIS_URL + "/2"

    # openapi
    API_PREFIX: str = f"/{APP_NAME}/api"
    WS_PREFIX: str = f"/{APP_NAME}/ws"
    DOCS_URL: str = API_PREFIX + "/docs/"
    OPENAPI_URL: str = f"{API_PREFIX}/openapi.json"

    # DEPLOY
    BACKEND_HOST: str = "localhost"
    BACKEND_PORT: int = 8080
    BACKEND_WORKERS: int = 1
    RELOAD: bool = False

    # LLM
    LLM_BASE_URL: str | None = None
    LLM_API_KEY: SecretStr
    LLM_MODEL_NAME: str

    # REPAIRS:
    FREQUENT_REPAIR_THRESHOLD: int = 3

from typing import Literal
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

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

    # external Databases
    ABAI_ASYNC_DATABASE_URL: str

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

    # S3 (minio)
    S3_ACCESS_KEY: str
    S3_SECRET_KEY: str
    S3_ENDPOINT_URL: str

    DYNAMOGRAM_BUCKET_NAME: str = "PRS-DYNAMOGRAM-BUCKET"
    SPO_BUCKET_NAME: str = "PRS-DYNAMOGRAM-BUCKET"  # спуско подъемные операции
    REPORTS_BUCKET_NAME: str = "PRS-REPORTS-BUCKET"

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

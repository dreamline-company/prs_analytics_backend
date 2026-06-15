from typing import Literal

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
    APP_NAME: str = "prs-effectiveness"

    # database
    APP_ASYNC_DATABASE_URL: SecretStr
    APP_SYNC_DATABASE_URL: SecretStr
    ALEMBIC_VERSION_TABLE_NAME: str = "prs_alembic_versions"

    ABAI_ASYNC_DATABASE_URL: str
    TELEMETRY_ASYNC_DATABASE_URL: str

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

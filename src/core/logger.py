import logging
import uuid
from functools import lru_cache

from core.settings import get_settings

settings = get_settings()


@lru_cache(maxsize=1)
def config_logger() -> None:
    # root logger for inheritance by third-party packages
    logging.basicConfig(
        level=logging.ERROR,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # App root logger for inheritance by app modules
    logger = logging.getLogger(settings.APP_NAME)
    logger.propagate = False
    if not logger.hasHandlers():
        logger.setLevel(getattr(logging, settings.LOG_LEVEL, logging.ERROR))
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            "%(asctime)s - %(levelname)s - %(name)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    logger.debug("Logger configured %s", uuid.uuid4())


@lru_cache
def get_logger(logger: str | None = None) -> logging.Logger:
    """Get cached logger"""
    config_logger()
    logger = f"{settings.APP_NAME}.{logger}" if logger else settings.APP_NAME
    return logging.getLogger(logger)

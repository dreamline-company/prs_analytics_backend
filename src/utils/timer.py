import asyncio
import time
from functools import wraps

from core import get_logger

logger = get_logger(__name__)


def timing(func):
    if asyncio.iscoroutinefunction(func):

        @wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.perf_counter()
            result = await func(*args, **kwargs)
            end_time = time.perf_counter()
            elapsed = end_time - start_time
            logger.debug(
                f"⏱️ Function '{func.__name__}' completed in {elapsed:.4f} seconds",
            )
            return result

    else:

        @wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.perf_counter()
            result = func(*args, **kwargs)
            end_time = time.perf_counter()
            elapsed = end_time - start_time
            logger.debug(
                f"⏱️ Function '{func.__name__}' completed in {elapsed:.4f} seconds",
            )
            return result

    return wrapper

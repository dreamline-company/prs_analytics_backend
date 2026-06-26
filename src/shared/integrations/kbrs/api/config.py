from __future__ import annotations

from dataclasses import dataclass

from .constants import DEFAULT_TOUCAN_PORT


@dataclass(frozen=True, slots=True)
class ToucanClientConfig:
    """Connection settings for the Toucan backend client."""

    host: str = "10.32.10.86"
    port: int = DEFAULT_TOUCAN_PORT
    timeout_seconds: int = 30
    user_agent: str = "Mozilla/4.0 (compatible; ICS)"
    http_path: str = "/service"
    keep_alive: bool = True

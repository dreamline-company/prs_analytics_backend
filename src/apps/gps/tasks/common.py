from datetime import datetime

from core.settings import get_settings
from shared.integrations.argus.api import ArgusClient

settings = get_settings()

UNITS_SYNC_TASK = "gps.units.sync"
TRACKS_LOAD_TASK = "gps.tracks.incremental"


def local_now() -> datetime:
    return datetime.now(tz=settings.ZONE_INFO).replace(tzinfo=None)


def argus_configured() -> bool:
    return bool(settings.ARGUS_LOGIN and settings.ARGUS_PASS)


def build_argus_client() -> ArgusClient:
    return ArgusClient(
        login=settings.ARGUS_LOGIN,
        password=settings.ARGUS_PASS,
        base_url=settings.ARGUS_BASE_URL,
        connect_to=settings.ARGUS_CONNECT_THROUGH,
    )

"""Реестр SDMO-источников: одна MySQL-база на НГДУ.

Схемы баз идентичны, различаются только данные, поэтому единственный параметр
источника — НГДУ (``AbaiNGDUIDsEnum``). Он же пишется в ``abai_ngdu_id``
станций и строк fc_data и вместе с натуральным ``sdmo_id`` образует ключ
станции внутри своего НГДУ.
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shared.constants.ngdu import AbaiNGDUIDsEnum
from shared.database.sql.setup import sdmo_engine_key, session_makers


class SdmoSourceNotConfiguredError(LookupError):
    """У НГДУ нет настроенной SDMO-базы (нет SDMO_<NGDU>_ASYNC_DATABASE_URL)."""

    def __init__(self, ngdu: AbaiNGDUIDsEnum) -> None:
        super().__init__(
            f"SDMO database for NGDU {ngdu.name} ({int(ngdu)}) is not configured",
        )
        self.ngdu = ngdu


def as_ngdu(abai_ngdu_id: int) -> AbaiNGDUIDsEnum:
    """Проверить и привести id НГДУ к enum; неизвестный id — ``ValueError``."""
    return AbaiNGDUIDsEnum(abai_ngdu_id)


def configured_ngdus() -> list[AbaiNGDUIDsEnum]:
    """НГДУ, у которых настроена SDMO-база, в порядке enum."""
    return [ngdu for ngdu in AbaiNGDUIDsEnum if sdmo_engine_key(ngdu) in session_makers]


def sdmo_session_maker(ngdu: AbaiNGDUIDsEnum) -> async_sessionmaker[AsyncSession]:
    key = sdmo_engine_key(ngdu)
    if key not in session_makers:
        raise SdmoSourceNotConfiguredError(ngdu)
    return session_makers[key]

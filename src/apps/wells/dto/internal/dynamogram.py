from datetime import datetime

from pydantic import BaseModel


class WellDynamogramDTO(BaseModel):
    """Динамограмма скважины со ссылкой на файл в S3.

    Ссылка временная (presigned): фронт тянет картинку из хранилища напрямую,
    не проксируя через сервис. Если запись файла потерялась, остаются только
    ``id``/``snapshot_time`` — сама динамограмма при этом не скрывается, чтобы
    дырка в хранилище была видна, а не выглядела как отсутствие замера.
    """

    id: int
    well_id: int
    snapshot_time: datetime
    file_id: int
    file_key: str | None = None
    download_url: str | None = None
    expires_at: datetime | None = None

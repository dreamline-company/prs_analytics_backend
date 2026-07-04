import datetime

from pydantic import BaseModel


class FileDownloadDTO(BaseModel):
    id: int
    key: str
    bucket: str
    download_url: str
    expires_at: datetime.datetime

"""Пакетное чтение из S3: один клиент на пачку, пропавшие ключи пропускаются."""

import asyncio
from contextlib import asynccontextmanager
from io import BytesIO

from botocore.exceptions import ClientError

from shared.database.s3.storage import AiobotoFileStorage


class _Client:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects

    async def download_fileobj(
        self,
        *,
        Bucket: str,  # noqa: ARG002, N803
        Key: str,  # noqa: N803
        Fileobj: BytesIO,  # noqa: N803
    ) -> None:
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        Fileobj.write(self.objects[Key])


def _storage(objects: dict[str, bytes]) -> tuple[AiobotoFileStorage, list[int]]:
    opened: list[int] = []

    @asynccontextmanager
    async def factory():  # noqa: ANN202
        opened.append(1)
        yield _Client(objects)

    return AiobotoFileStorage(bucket_name="b", client_factory=factory), opened


def test_download_files_uses_one_client_and_skips_missing() -> None:
    storage, opened = _storage({"a": b"1", "c": b"3"})

    result = asyncio.run(storage.download_files(["a", "b", "c", "a"]))

    assert {path: f.read() for path, f in result.items()} == {"a": b"1", "c": b"3"}
    assert len(opened) == 1


def test_download_files_without_paths_opens_no_client() -> None:
    storage, opened = _storage({})

    assert asyncio.run(storage.download_files([])) == {}
    assert opened == []

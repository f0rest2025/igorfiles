import asyncio

import pytest

from app.upload_temp import EmptyUploadError, UploadTooLargeError, save_upload_to_temp


class FakeUpload:
    def __init__(self, data: bytes, filename: str = "file.bin") -> None:
        self._data = data
        self._offset = 0
        self.filename = filename

    async def read(self, size: int) -> bytes:
        if self._offset >= len(self._data):
            return b""
        chunk = self._data[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


def test_save_upload_to_temp_writes_file_and_cleans_up():
    upload = FakeUpload(b"payload", "big.bin")

    saved = asyncio.run(save_upload_to_temp(upload))

    assert saved.size == 7
    assert saved.path.read_bytes() == b"payload"
    saved.cleanup()
    assert not saved.path.exists()


def test_save_upload_to_temp_rejects_empty_file():
    upload = FakeUpload(b"", "empty.bin")

    with pytest.raises(EmptyUploadError):
        asyncio.run(save_upload_to_temp(upload))


def test_save_upload_to_temp_rejects_size_limit():
    upload = FakeUpload(b"payload", "too-big.bin")

    with pytest.raises(UploadTooLargeError):
        asyncio.run(save_upload_to_temp(upload, max_size_bytes=3))

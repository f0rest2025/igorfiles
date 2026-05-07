from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile

from app.diagnostics import get_logger


CHUNK_SIZE = 1024 * 1024
logger = get_logger(__name__)


class EmptyUploadError(ValueError):
    pass


class UploadTooLargeError(ValueError):
    def __init__(self, limit: int) -> None:
        super().__init__(f"Файл больше разрешённого лимита: {limit} байт")
        self.limit = limit


@dataclass(slots=True)
class SavedUpload:
    path: Path
    size: int
    filename: str

    def cleanup(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            return


async def save_upload_to_temp(upload: UploadFile, *, max_size_bytes: int = 0) -> SavedUpload:
    fd, raw_path = tempfile.mkstemp(prefix="yos-upload-", suffix=".tmp")
    path = Path(raw_path)
    size = 0
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = await upload.read(CHUNK_SIZE)
                if not chunk:
                    break
                size += len(chunk)
                if max_size_bytes and size > max_size_bytes:
                    raise UploadTooLargeError(max_size_bytes)
                out.write(chunk)
        if size == 0:
            raise EmptyUploadError("Файл пустой")
        logger.info("temp file saved path=%s size=%s filename=%s", path, size, _safe_filename(upload.filename or ""))
        return SavedUpload(path=path, size=size, filename=upload.filename or "file")
    except Exception:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def _safe_filename(value: str) -> str:
    return value.replace("\\", "_").replace("/", "_").replace('"', "_")[:180]

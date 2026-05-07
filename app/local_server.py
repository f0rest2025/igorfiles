from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable

import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, Response

from app.client_page import render_local_upload_page
from app.config import AppConfig
from app.diagnostics import get_logger
from app.storage import StorageError, YandexStorageClient
from app.upload_temp import EmptyUploadError, UploadTooLargeError, save_upload_to_temp
from app.upload_tokens import DownloadTokenStore, UploadTokenStore


logger = get_logger(__name__)


@dataclass(slots=True)
class LocalServerState:
    config_provider: Callable[[], AppConfig]
    uploads: UploadTokenStore = field(default_factory=UploadTokenStore)
    downloads: DownloadTokenStore = field(default_factory=DownloadTokenStore)

    def storage(self) -> YandexStorageClient:
        return YandexStorageClient(self.config_provider())


def create_local_app(state: LocalServerState) -> FastAPI:
    app = FastAPI(title="Yandex Storage Local Upload Endpoint", docs_url=None, redoc_url=None)

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.get("/upload/{token}", response_class=HTMLResponse)
    async def upload_page(token: str) -> HTMLResponse:
        record = state.uploads.get(token)
        if record is None:
            raise HTTPException(status_code=404, detail="Ссылка недействительна или истекла")
        return HTMLResponse(render_local_upload_page(token, record.expires_at.strftime("%Y-%m-%d %H:%M:%S UTC")))

    @app.post("/upload/{token}")
    async def upload_submit(token: str, file: UploadFile = File(...)):
        logger.info("client upload consume start token=%s", _short_token(token))
        record = state.uploads.get(token)
        if record is None:
            raise HTTPException(status_code=404, detail="Upload token недействителен или истёк")
        actual_content_type = file.content_type or ""
        if record.expected_file_type and not _matches_mime(actual_content_type, record.expected_file_type):
            raise HTTPException(status_code=400, detail="Выбран файл неподходящего типа")
        try:
            saved = await save_upload_to_temp(file, max_size_bytes=record.max_size_bytes)
        except EmptyUploadError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except UploadTooLargeError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        try:
            result = state.storage().upload_file(saved.path, record.object_key, record.content_type or actual_content_type)
        except StorageError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        finally:
            saved.cleanup()
        state.uploads.mark_used(token)
        logger.info("client upload consume ok token=%s key=%s size=%s", _short_token(token), record.object_key, result.size)
        return {"ok": True, "message": "Файл загружен"}

    @app.get("/download/{token}")
    async def download(token: str) -> Response:
        record = state.downloads.get(token)
        if record is None:
            raise HTTPException(status_code=404, detail="Download token недействителен или истёк")
        try:
            result = state.storage().download_object(record.object_key)
        except StorageError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        state.downloads.mark_used(token)
        headers = {"Content-Disposition": f'attachment; filename="{_safe_filename(result.filename)}"'}
        return Response(result.data, media_type=result.content_type, headers=headers)

    return app


class LocalServerRunner:
    def __init__(self, state: LocalServerState, host: str, port: int) -> None:
        self.state = state
        self.host = host
        self.port = port
        self.server: uvicorn.Server | None = None
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        app = create_local_app(self.state)
        config = uvicorn.Config(
            app,
            host=self.host,
            port=self.port,
            log_level="warning",
            access_log=False,
            log_config=None,
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        logger.info("local upload server start host=%s port=%s", self.host, self.port)

    def stop(self) -> None:
        if self.server:
            self.server.should_exit = True
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3)
        logger.info("local upload server stop host=%s port=%s", self.host, self.port)


def _matches_mime(actual: str, expected: str) -> bool:
    if not expected:
        return True
    if not actual:
        return False
    if expected.endswith("/*"):
        return actual.startswith(expected[:-1])
    return actual == expected


def _short_token(token: str) -> str:
    return token[:8] + "***"


def _safe_filename(value: str) -> str:
    return value.replace("\\", "_").replace("/", "_").replace('"', "_") or "download"

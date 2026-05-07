from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app.client_page import build_data_upload_url, render_local_upload_page
from app.config import AppConfig, ConfigError, default_config_path, delete_config, load_config, save_config
from app.models import (
    ConfigPayload,
    ConfigResponse,
    DirectUploadResponse,
    ObjectsResponse,
    PresignDownloadRequest,
    PresignDownloadResponse,
    PresignUploadRequest,
    PresignUploadResponse,
    StatusResponse,
)
from app.object_key import build_object_key, normalize_prefix
from app.storage import StorageError, YandexStorageClient, upload_bytes_via_client
from app.upload_tokens import DownloadTokenStore, UploadTokenStore


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"


class AppState:
    def __init__(self) -> None:
        try:
            self.config = load_config()
            self.config_error = ""
        except ConfigError as exc:
            self.config = AppConfig()
            self.config_error = str(exc)
        self.tokens = UploadTokenStore()
        self.downloads = DownloadTokenStore()

    def update_config(self, payload: ConfigPayload) -> AppConfig:
        incoming = AppConfig.from_mapping(model_to_dict(payload))
        self.config = self.config.merged_with(incoming, preserve_blank_secret=True)
        return self.config

    def storage(self) -> YandexStorageClient:
        return YandexStorageClient(self.config)


state = AppState()
app = FastAPI(title="Yandex Object Storage File Manager", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def model_to_dict(model):
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


@app.exception_handler(ConfigError)
async def config_error_handler(_: Request, exc: ConfigError) -> JSONResponse:
    return JSONResponse(status_code=400, content=model_to_dict(StatusResponse(ok=False, message=str(exc))))


@app.exception_handler(StorageError)
async def storage_error_handler(_: Request, exc: StorageError) -> JSONResponse:
    return JSONResponse(status_code=502, content=model_to_dict(StatusResponse(ok=False, message=str(exc))))


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health", response_model=StatusResponse)
async def health() -> StatusResponse:
    details = {"config_path": str(default_config_path())}
    if state.config_error:
        details["config_error"] = state.config_error
    return StatusResponse(ok=True, message="Сервис запущен", details=details)


@app.get("/api/config", response_model=ConfigResponse)
async def get_config() -> ConfigResponse:
    data = state.config.public_dict()
    data["config_path"] = str(default_config_path())
    return ConfigResponse(**data)


@app.post("/api/config/apply", response_model=StatusResponse)
async def apply_config(payload: ConfigPayload) -> StatusResponse:
    state.update_config(payload)
    return StatusResponse(ok=True, message="Настройки применены")


@app.post("/api/config/save", response_model=StatusResponse)
async def persist_config(payload: ConfigPayload) -> StatusResponse:
    config = state.update_config(payload)
    path = save_config(config)
    return StatusResponse(ok=True, message=f"Настройки сохранены: {path}")


@app.post("/api/config/test", response_model=StatusResponse)
async def test_config(payload: ConfigPayload) -> StatusResponse:
    config = state.config.merged_with(AppConfig.from_mapping(model_to_dict(payload)), preserve_blank_secret=True)
    YandexStorageClient(config).test_connection()
    state.config = config
    return StatusResponse(ok=True, message="Подключение к bucket успешно проверено")


@app.delete("/api/config", response_model=StatusResponse)
async def clear_config() -> StatusResponse:
    state.config = AppConfig()
    delete_config()
    return StatusResponse(ok=True, message="Настройки очищены")


@app.get("/api/objects", response_model=ObjectsResponse)
async def list_objects(prefix: str = "") -> ObjectsResponse:
    effective_prefix = normalize_prefix(prefix or state.config.prefix)
    objects = state.storage().list_objects(effective_prefix)
    return ObjectsResponse(objects=objects)


@app.post("/api/objects/presign-upload", response_model=PresignUploadResponse)
async def presign_upload(payload: PresignUploadRequest, request: Request) -> PresignUploadResponse:
    object_key = build_object_key(
        payload.object_name,
        payload.prefix or state.config.prefix,
        add_guid=payload.add_guid,
        sanitize=payload.sanitize,
    )
    content_type = (payload.content_type or payload.expected_file_type or "").strip()
    expected_file_type = (payload.expected_file_type or "").strip()
    upload_url = ""
    client_data_url = ""
    if state.config.uses_legacy_static_keys:
        upload_url = state.storage().presign_upload(object_key, payload.expires_in, content_type=content_type)
        client_data_url = build_data_upload_url(upload_url, content_type=content_type, expected_file_type=expected_file_type)
    token = state.tokens.create(
        object_key,
        payload.expires_in,
        content_type=content_type,
        expected_file_type=expected_file_type,
        max_size_bytes=payload.max_size_bytes,
    )
    client_url = str(request.url_for("local_upload_page", token=token.token))
    return PresignUploadResponse(
        object_key=object_key,
        upload_url=upload_url,
        client_url=client_url,
        client_data_url=client_data_url,
        expires_at=token.expires_at,
    )


@app.post("/api/objects/presign-download", response_model=PresignDownloadResponse)
async def presign_download(payload: PresignDownloadRequest, request: Request) -> PresignDownloadResponse:
    if state.config.uses_legacy_static_keys:
        url = state.storage().presign_download(payload.object_key, payload.expires_in)
    else:
        token = state.downloads.create(payload.object_key, payload.expires_in)
        url = str(request.url_for("local_download", token=token.token))
    expires_at = datetime.now(UTC) + timedelta(seconds=payload.expires_in)
    return PresignDownloadResponse(object_key=payload.object_key, download_url=url, expires_at=expires_at)


@app.post("/api/objects/upload-direct", response_model=DirectUploadResponse)
async def upload_direct(
    file: UploadFile = File(...),
    prefix: str = Form(""),
    object_name: str = Form(""),
    add_guid: bool = Form(False),
    sanitize: bool = Form(True),
) -> DirectUploadResponse:
    name = object_name.strip() or file.filename or "file"
    object_key = build_object_key(name, prefix or state.config.prefix, add_guid=add_guid, sanitize=sanitize)
    result = state.storage().upload_direct(file.file, object_key, content_type=file.content_type or "")
    return DirectUploadResponse(object_key=result.object_key, size=result.size, etag=result.etag)


@app.get("/upload/{token}", response_class=HTMLResponse, name="local_upload_page", include_in_schema=False)
async def local_upload_page(token: str) -> HTMLResponse:
    record = state.tokens.get(token)
    if record is None:
        raise HTTPException(status_code=404, detail="Ссылка недействительна или истекла")
    return HTMLResponse(render_local_upload_page(token, record.expires_at.strftime("%Y-%m-%d %H:%M:%S UTC")))


@app.post("/upload/{token}", response_model=StatusResponse, include_in_schema=False)
async def local_upload_submit(token: str, file: UploadFile = File(...)) -> StatusResponse:
    record = state.tokens.get(token)
    if record is None:
        raise HTTPException(status_code=404, detail="Ссылка недействительна или истекла")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Файл пустой")
    if record.expected_file_type and not _matches_mime(file.content_type or "", record.expected_file_type):
        raise HTTPException(status_code=400, detail="Выбран файл неподходящего типа")
    if record.max_size_bytes and len(data) > record.max_size_bytes:
        raise HTTPException(status_code=413, detail=f"Файл больше разрешённого лимита: {record.max_size_bytes} байт")
    upload_bytes_via_client(state.storage(), record.object_key, data, content_type=record.content_type or file.content_type or "")
    state.tokens.mark_used(token)
    return StatusResponse(ok=True, message="Файл загружен")


@app.get("/download/{token}", name="local_download", include_in_schema=False)
async def local_download(token: str) -> Response:
    record = state.downloads.get(token)
    if record is None:
        raise HTTPException(status_code=404, detail="Download token недействителен или истёк")
    result = state.storage().download_object(record.object_key)
    state.downloads.mark_used(token)
    filename = result.filename.replace("\\", "_").replace("/", "_").replace('"', "_") or "download"
    return Response(
        result.data,
        media_type=result.content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _matches_mime(actual: str, expected: str) -> bool:
    if not expected:
        return True
    if not actual:
        return False
    if expected.endswith("/*"):
        return actual.startswith(expected[:-1])
    return actual == expected


def run() -> None:
    uvicorn.run("app.main:app", host="127.0.0.1", port=8765, reload=False)


if __name__ == "__main__":
    run()

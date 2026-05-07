from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.config import AuthMode, DEFAULT_ENDPOINT, DEFAULT_REGION


MAX_EXPIRES_IN = 604800


class ConfigPayload(BaseModel):
    version: int = 2
    access_key_id: str = ""
    secret_key: str = ""
    bucket: str = ""
    prefix: str = ""
    endpoint: str = DEFAULT_ENDPOINT
    region: str = DEFAULT_REGION
    auth_mode: str = AuthMode.YC_CLI.value
    yc_profile: str = ""
    service_account_key_path: str = ""
    upload_server_bind_host: str = "127.0.0.1"
    upload_server_port: int = 8765
    public_base_url: str = "http://127.0.0.1:8765"
    debug: bool = False


class StatusResponse(BaseModel):
    ok: bool
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ConfigResponse(ConfigPayload):
    has_secret_key: bool = False
    config_path: str


class ObjectInfo(BaseModel):
    key: str
    size: int
    last_modified: datetime | None = None
    storage_class: str = ""
    etag: str = ""


class ObjectsResponse(BaseModel):
    objects: list[ObjectInfo]


class ExpiresMixin(BaseModel):
    expires_in: int = Field(default=3600, ge=60, le=MAX_EXPIRES_IN)


class PresignUploadRequest(ExpiresMixin):
    object_name: str = Field(min_length=1)
    prefix: str = ""
    content_type: str = ""
    add_guid: bool = True
    sanitize: bool = True
    expected_file_type: str = ""
    max_size_bytes: int = Field(default=0, ge=0)


class PresignUploadResponse(BaseModel):
    object_key: str
    client_url: str
    upload_url: str = ""
    client_data_url: str = ""
    expires_at: datetime


class PresignDownloadRequest(ExpiresMixin):
    object_key: str = Field(min_length=1)


class PresignDownloadResponse(BaseModel):
    object_key: str
    download_url: str
    expires_at: datetime


class DirectUploadResponse(BaseModel):
    object_key: str
    size: int
    etag: str = ""

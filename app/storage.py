from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import BinaryIO

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

from app.config import AppConfig, ConfigError
from app.models import ObjectInfo


class StorageError(RuntimeError):
    pass


@dataclass(slots=True)
class UploadResult:
    object_key: str
    size: int
    etag: str = ""


class YandexStorageClient:
    def __init__(self, config: AppConfig):
        self.config = config

    def _client(self):
        self.config.require_ready()
        return boto3.client(
            "s3",
            endpoint_url=self.config.endpoint,
            region_name=self.config.region,
            aws_access_key_id=self.config.access_key_id,
            aws_secret_access_key=self.config.secret_key,
            config=BotoConfig(signature_version="s3v4"),
        )

    def test_connection(self) -> None:
        try:
            self._client().head_bucket(Bucket=self.config.bucket)
        except ConfigError:
            raise
        except (NoCredentialsError, ClientError, BotoCoreError) as exc:
            raise StorageError(_safe_boto_message(exc, "Не удалось проверить подключение к bucket")) from exc

    def list_objects(self, prefix: str = "") -> list[ObjectInfo]:
        try:
            client = self._client()
            paginator = client.get_paginator("list_objects_v2")
            items: list[ObjectInfo] = []
            for page in paginator.paginate(Bucket=self.config.bucket, Prefix=prefix):
                for raw in page.get("Contents", []):
                    items.append(
                        ObjectInfo(
                            key=raw.get("Key", ""),
                            size=int(raw.get("Size") or 0),
                            last_modified=raw.get("LastModified"),
                            storage_class=raw.get("StorageClass") or "",
                            etag=(raw.get("ETag") or "").strip('"'),
                        )
                    )
            return items
        except ConfigError:
            raise
        except (NoCredentialsError, ClientError, BotoCoreError) as exc:
            raise StorageError(_safe_boto_message(exc, "Не удалось получить список объектов")) from exc

    def presign_upload(self, object_key: str, expires_in: int, content_type: str = "") -> str:
        params = {"Bucket": self.config.bucket, "Key": object_key}
        if content_type:
            params["ContentType"] = content_type
        try:
            return self._client().generate_presigned_url(
                "put_object",
                Params=params,
                ExpiresIn=expires_in,
                HttpMethod="PUT",
            )
        except ConfigError:
            raise
        except (NoCredentialsError, ClientError, BotoCoreError) as exc:
            raise StorageError(_safe_boto_message(exc, "Не удалось сгенерировать upload-ссылку")) from exc

    def presign_download(self, object_key: str, expires_in: int) -> str:
        try:
            return self._client().generate_presigned_url(
                "get_object",
                Params={"Bucket": self.config.bucket, "Key": object_key},
                ExpiresIn=expires_in,
                HttpMethod="GET",
            )
        except ConfigError:
            raise
        except (NoCredentialsError, ClientError, BotoCoreError) as exc:
            raise StorageError(_safe_boto_message(exc, "Не удалось сгенерировать download-ссылку")) from exc

    def upload_direct(self, file_obj: BinaryIO, object_key: str, content_type: str = "") -> UploadResult:
        extra_args = {}
        if content_type:
            extra_args["ContentType"] = content_type
        try:
            client = self._client()
            client.upload_fileobj(file_obj, self.config.bucket, object_key, ExtraArgs=extra_args or None)
            head = client.head_object(Bucket=self.config.bucket, Key=object_key)
            return UploadResult(
                object_key=object_key,
                size=int(head.get("ContentLength") or 0),
                etag=(head.get("ETag") or "").strip('"'),
            )
        except ConfigError:
            raise
        except (NoCredentialsError, ClientError, BotoCoreError) as exc:
            raise StorageError(_safe_boto_message(exc, "Не удалось загрузить файл")) from exc


def upload_bytes_to_presigned_url(upload_url: str, data: bytes, content_type: str = "") -> None:
    headers = {"Content-Length": str(len(data))}
    if content_type:
        headers["Content-Type"] = content_type
    request = urllib.request.Request(upload_url, data=data, headers=headers, method="PUT")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            if response.status >= 400:
                raise StorageError(f"Object Storage вернул HTTP {response.status}")
    except urllib.error.HTTPError as exc:
        body = exc.read(800).decode("utf-8", errors="replace")
        raise StorageError(f"Object Storage вернул HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise StorageError(f"Не удалось выполнить upload по pre-signed URL: {exc.reason}") from exc


def _safe_boto_message(exc: Exception, fallback: str) -> str:
    if isinstance(exc, ClientError):
        error = exc.response.get("Error", {})
        code = error.get("Code") or "Unknown"
        message = error.get("Message") or fallback
        return f"{fallback}: {code}: {message}"
    if isinstance(exc, NoCredentialsError):
        return "Не указаны ключи доступа"
    return f"{fallback}: {exc}"


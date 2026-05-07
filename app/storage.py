from __future__ import annotations

import os
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Callable

import boto3
from boto3.exceptions import S3UploadFailedError
from boto3.s3.transfer import TransferConfig
from botocore import UNSIGNED
from botocore.client import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

from app.config import AppConfig, AuthMode, ConfigError
from app.diagnostics import get_logger, redact
from app.iam import TokenProvider, create_token_provider
from app.models import ObjectInfo


logger = get_logger(__name__)

MULTIPART_CHUNK_SIZE = 8 * 1024 * 1024
MULTIPART_THRESHOLD = 8 * 1024 * 1024
TRANSFER_CONFIG = TransferConfig(
    multipart_threshold=MULTIPART_THRESHOLD,
    multipart_chunksize=MULTIPART_CHUNK_SIZE,
    max_concurrency=3,
    use_threads=True,
)
BOTO_RETRIES = {"max_attempts": 5, "mode": "standard"}
BOTO_S3_OPTIONS = {"addressing_style": "path"}
BOTO_SIGNED_CONFIG = BotoConfig(
    signature_version="s3v4",
    connect_timeout=30,
    read_timeout=1800,
    retries=BOTO_RETRIES,
    s3=BOTO_S3_OPTIONS,
)
BOTO_IAM_CONFIG = BotoConfig(
    signature_version=UNSIGNED,
    connect_timeout=30,
    read_timeout=1800,
    retries=BOTO_RETRIES,
    s3=BOTO_S3_OPTIONS,
)

ProgressCallback = Callable[[int, int | None], None]


class StorageError(RuntimeError):
    pass


@dataclass(slots=True)
class UploadResult:
    object_key: str
    size: int
    etag: str = ""


@dataclass(slots=True)
class DownloadResult:
    object_key: str
    data: bytes
    content_type: str = "application/octet-stream"
    filename: str = "download"


class YandexStorageClient:
    def __init__(self, config: AppConfig, token_provider: TokenProvider | None = None):
        self.config = config
        self.token_provider = token_provider if token_provider is not None else create_token_provider(config)
        if self.config.auth_mode == AuthMode.LEGACY_STATIC.value:
            self.backend: StorageBackend = LegacyStaticStorageBackend(config)
        else:
            if self.token_provider is None:
                raise ConfigError("Не удалось создать IAM token provider")
            self.backend = IamHttpStorageBackend(config, self.token_provider)

    def test_connection(self) -> None:
        return self.backend.test_connection()

    def list_objects(self, prefix: str = "") -> list[ObjectInfo]:
        return self.backend.list_objects(prefix)

    def upload_direct(
        self,
        file_obj: BinaryIO,
        object_key: str,
        content_type: str = "",
        progress_callback: ProgressCallback | None = None,
    ) -> UploadResult:
        return self.backend.upload_direct(file_obj, object_key, content_type, progress_callback)

    def upload_file(
        self,
        file_path: str | Path,
        object_key: str,
        content_type: str = "",
        progress_callback: ProgressCallback | None = None,
    ) -> UploadResult:
        return self.backend.upload_file(file_path, object_key, content_type, progress_callback)

    def download_object(self, object_key: str) -> DownloadResult:
        return self.backend.download_object(object_key)

    def presign_upload(self, object_key: str, expires_in: int, content_type: str = "") -> str:
        return self.backend.presign_upload(object_key, expires_in, content_type)

    def presign_download(self, object_key: str, expires_in: int) -> str:
        return self.backend.presign_download(object_key, expires_in)


class StorageBackend:
    def test_connection(self) -> None:
        raise NotImplementedError

    def list_objects(self, prefix: str = "") -> list[ObjectInfo]:
        raise NotImplementedError

    def upload_direct(
        self,
        file_obj: BinaryIO,
        object_key: str,
        content_type: str = "",
        progress_callback: ProgressCallback | None = None,
    ) -> UploadResult:
        raise NotImplementedError

    def upload_file(
        self,
        file_path: str | Path,
        object_key: str,
        content_type: str = "",
        progress_callback: ProgressCallback | None = None,
    ) -> UploadResult:
        with Path(file_path).open("rb") as file_obj:
            return self.upload_direct(file_obj, object_key, content_type, progress_callback)

    def download_object(self, object_key: str) -> DownloadResult:
        raise NotImplementedError

    def presign_upload(self, object_key: str, expires_in: int, content_type: str = "") -> str:
        raise StorageError("Presigned PUT доступен только в Legacy static access key mode")

    def presign_download(self, object_key: str, expires_in: int) -> str:
        raise StorageError("Presigned GET доступен только в Legacy static access key mode")


class IamHttpStorageBackend(StorageBackend):
    def __init__(self, config: AppConfig, token_provider: TokenProvider) -> None:
        self.config = config
        self.token_provider = token_provider

    def test_connection(self) -> None:
        self.config.require_ready()
        logger.info("bucket check start auth=%s bucket=%s endpoint=%s", self.config.auth_mode, self.config.bucket, self.config.endpoint)
        self._request("HEAD", self._bucket_url(), expected={200})
        logger.info("bucket check ok bucket=%s", self.config.bucket)

    def list_objects(self, prefix: str = "") -> list[ObjectInfo]:
        self.config.require_ready()
        logger.info("object list start bucket=%s prefix=%s", self.config.bucket, prefix)
        query = urllib.parse.urlencode({"list-type": "2", "prefix": prefix or ""})
        body, _headers, _status = self._request("GET", self._bucket_url() + "?" + query)
        objects = _parse_list_objects(body)
        logger.info("object list ok bucket=%s prefix=%s count=%s", self.config.bucket, prefix, len(objects))
        return objects

    def upload_direct(
        self,
        file_obj: BinaryIO,
        object_key: str,
        content_type: str = "",
        progress_callback: ProgressCallback | None = None,
    ) -> UploadResult:
        self.config.require_ready()
        size = _fileobj_size(file_obj)
        return self._upload_fileobj(file_obj, object_key, content_type, size, progress_callback)

    def upload_file(
        self,
        file_path: str | Path,
        object_key: str,
        content_type: str = "",
        progress_callback: ProgressCallback | None = None,
    ) -> UploadResult:
        self.config.require_ready()
        path = Path(file_path)
        size = path.stat().st_size
        with path.open("rb") as file_obj:
            return self._upload_fileobj(file_obj, object_key, content_type, size, progress_callback)

    def _upload_fileobj(
        self,
        file_obj: BinaryIO,
        object_key: str,
        content_type: str,
        size: int | None,
        progress_callback: ProgressCallback | None,
    ) -> UploadResult:
        extra_args = _extra_args(content_type)
        logger.info("object storage upload started auth=%s bucket=%s key=%s size=%s", self.config.auth_mode, self.config.bucket, object_key, size)
        if size is not None and size >= MULTIPART_THRESHOLD:
            logger.info(
                "multipart upload started bucket=%s key=%s size=%s chunk_size=%s max_concurrency=%s",
                self.config.bucket,
                object_key,
                size,
                MULTIPART_CHUNK_SIZE,
                TRANSFER_CONFIG.max_concurrency,
            )
        tracker = UploadProgressTracker(object_key, size, progress_callback)
        started = time.monotonic()
        try:
            self._client().upload_fileobj(
                file_obj,
                self.config.bucket,
                object_key,
                ExtraArgs=extra_args or None,
                Config=TRANSFER_CONFIG,
                Callback=tracker,
            )
            head = self._client().head_object(Bucket=self.config.bucket, Key=object_key)
            elapsed = time.monotonic() - started
            result = UploadResult(
                object_key=object_key,
                size=int(head.get("ContentLength") or size or tracker.bytes_seen),
                etag=(head.get("ETag") or "").strip('"'),
            )
            logger.info("upload completed bucket=%s key=%s size=%s elapsed=%.2fs", self.config.bucket, object_key, result.size, elapsed)
            return result
        except ConfigError:
            raise
        except (S3UploadFailedError, NoCredentialsError, ClientError, BotoCoreError, OSError, socket.timeout, TimeoutError, ssl.SSLError) as exc:
            logger.exception("upload failed exception=%s bucket=%s key=%s", exc.__class__.__name__, self.config.bucket, object_key)
            raise StorageError(_friendly_upload_exception(exc)) from exc

    def download_object(self, object_key: str) -> DownloadResult:
        self.config.require_ready()
        logger.info("object download start bucket=%s key=%s", self.config.bucket, object_key)
        body, headers, _status = self._request("GET", self._object_url(object_key))
        content_type = headers.get("Content-Type") or headers.get("content-type") or "application/octet-stream"
        logger.info("object download ok bucket=%s key=%s size=%s", self.config.bucket, object_key, len(body))
        return DownloadResult(object_key=object_key, data=body, content_type=content_type, filename=object_key.rsplit("/", 1)[-1] or "download")

    def _request(
        self,
        method: str,
        url: str,
        *,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        expected: set[int] | None = None,
    ) -> tuple[bytes, dict[str, str], int]:
        token = self.token_provider.get_token()
        request_headers = dict(headers or {})
        request_headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
        expected = expected or {200}
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                body = response.read()
                status = response.status
                if status not in expected:
                    raise StorageError(f"Object Storage вернул HTTP {status}")
                return body, dict(response.headers.items()), status
        except urllib.error.HTTPError as exc:
            body = exc.read(1200).decode("utf-8", errors="replace")
            message = _friendly_http_error(exc.code, body)
            logger.warning("storage http error method=%s url=%s status=%s body=%s", method, _safe_url(url), exc.code, redact(body))
            raise StorageError(message) from exc
        except urllib.error.URLError as exc:
            raise StorageError(f"Не удалось подключиться к Object Storage: {exc.reason}") from exc

    def _bucket_url(self) -> str:
        return f"{self.config.endpoint.rstrip('/')}/{urllib.parse.quote(self.config.bucket, safe='')}"

    def _object_url(self, object_key: str) -> str:
        return f"{self._bucket_url()}/{urllib.parse.quote(object_key, safe='/')}"

    def _client(self):
        client = boto3.client(
            "s3",
            endpoint_url=self.config.endpoint,
            region_name=self.config.region,
            aws_access_key_id="iam",
            aws_secret_access_key="iam",
            config=BOTO_IAM_CONFIG,
        )
        client.meta.events.register("before-send.s3", self._add_iam_authorization)
        return client

    def _add_iam_authorization(self, request, **_kwargs) -> None:
        request.headers["Authorization"] = f"Bearer {self.token_provider.get_token()}"


class LegacyStaticStorageBackend(StorageBackend):
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
            config=BOTO_SIGNED_CONFIG,
        )

    def test_connection(self) -> None:
        logger.info("legacy bucket check start bucket=%s endpoint=%s", self.config.bucket, self.config.endpoint)
        try:
            self._client().head_bucket(Bucket=self.config.bucket)
        except ConfigError:
            raise
        except (NoCredentialsError, ClientError, BotoCoreError) as exc:
            raise StorageError(_safe_boto_message(exc, "Не удалось проверить подключение к bucket")) from exc
        logger.info("legacy bucket check ok bucket=%s", self.config.bucket)

    def list_objects(self, prefix: str = "") -> list[ObjectInfo]:
        logger.info("legacy object list start bucket=%s prefix=%s", self.config.bucket, prefix)
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
            logger.info("legacy object list ok bucket=%s prefix=%s count=%s", self.config.bucket, prefix, len(items))
            return items
        except ConfigError:
            raise
        except (NoCredentialsError, ClientError, BotoCoreError) as exc:
            raise StorageError(_safe_boto_message(exc, "Не удалось получить список объектов")) from exc

    def presign_upload(self, object_key: str, expires_in: int, content_type: str = "") -> str:
        params = {"Bucket": self.config.bucket, "Key": object_key}
        if content_type:
            params["ContentType"] = content_type
        logger.info("legacy presigned put generation key=%s ttl=%s", object_key, expires_in)
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
            raise StorageError(_safe_boto_message(exc, "Не удалось сгенерировать legacy upload-ссылку")) from exc

    def presign_download(self, object_key: str, expires_in: int) -> str:
        logger.info("legacy presigned get generation key=%s ttl=%s", object_key, expires_in)
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
            raise StorageError(_safe_boto_message(exc, "Не удалось сгенерировать legacy download-ссылку")) from exc

    def upload_direct(
        self,
        file_obj: BinaryIO,
        object_key: str,
        content_type: str = "",
        progress_callback: ProgressCallback | None = None,
    ) -> UploadResult:
        extra_args = _extra_args(content_type)
        size = _fileobj_size(file_obj)
        logger.info("legacy direct upload start bucket=%s key=%s", self.config.bucket, object_key)
        if size is not None and size >= MULTIPART_THRESHOLD:
            logger.info(
                "multipart upload started bucket=%s key=%s size=%s chunk_size=%s max_concurrency=%s",
                self.config.bucket,
                object_key,
                size,
                MULTIPART_CHUNK_SIZE,
                TRANSFER_CONFIG.max_concurrency,
            )
        tracker = UploadProgressTracker(object_key, size, progress_callback)
        try:
            client = self._client()
            client.upload_fileobj(
                file_obj,
                self.config.bucket,
                object_key,
                ExtraArgs=extra_args or None,
                Config=TRANSFER_CONFIG,
                Callback=tracker,
            )
            head = client.head_object(Bucket=self.config.bucket, Key=object_key)
            result = UploadResult(
                object_key=object_key,
                size=int(head.get("ContentLength") or size or tracker.bytes_seen),
                etag=(head.get("ETag") or "").strip('"'),
            )
            logger.info("upload completed bucket=%s key=%s size=%s", self.config.bucket, object_key, result.size)
            return result
        except ConfigError:
            raise
        except (S3UploadFailedError, NoCredentialsError, ClientError, BotoCoreError, OSError, socket.timeout, TimeoutError, ssl.SSLError) as exc:
            logger.exception("upload failed exception=%s bucket=%s key=%s", exc.__class__.__name__, self.config.bucket, object_key)
            raise StorageError(_friendly_upload_exception(exc)) from exc

    def upload_file(
        self,
        file_path: str | Path,
        object_key: str,
        content_type: str = "",
        progress_callback: ProgressCallback | None = None,
    ) -> UploadResult:
        self.config.require_ready()
        path = Path(file_path)
        size = path.stat().st_size
        with path.open("rb") as file_obj:
            return self.upload_direct(file_obj, object_key, content_type, progress_callback)

    def download_object(self, object_key: str) -> DownloadResult:
        logger.info("legacy object download start bucket=%s key=%s", self.config.bucket, object_key)
        try:
            response = self._client().get_object(Bucket=self.config.bucket, Key=object_key)
            data = response["Body"].read()
            content_type = response.get("ContentType") or "application/octet-stream"
            return DownloadResult(object_key=object_key, data=data, content_type=content_type, filename=object_key.rsplit("/", 1)[-1] or "download")
        except ConfigError:
            raise
        except (NoCredentialsError, ClientError, BotoCoreError) as exc:
            raise StorageError(_safe_boto_message(exc, "Не удалось скачать объект")) from exc


class UploadProgressTracker:
    def __init__(self, object_key: str, total_bytes: int | None, callback: ProgressCallback | None = None) -> None:
        self.object_key = object_key
        self.total_bytes = total_bytes
        self.callback = callback
        self.bytes_seen = 0
        self._last_logged = 0
        self._lock = threading.Lock()

    def __call__(self, bytes_amount: int) -> None:
        with self._lock:
            self.bytes_seen += bytes_amount
            current = self.bytes_seen
            should_log = current == self.total_bytes or current - self._last_logged >= MULTIPART_CHUNK_SIZE
            if should_log:
                self._last_logged = current
                logger.info("uploaded bytes key=%s bytes=%s total=%s", self.object_key, current, self.total_bytes)
        if self.callback:
            self.callback(current, self.total_bytes)


def _fileobj_size(file_obj: BinaryIO) -> int | None:
    try:
        current = file_obj.tell()
        file_obj.seek(0, os.SEEK_END)
        size = file_obj.tell()
        file_obj.seek(current, os.SEEK_SET)
        return int(size)
    except (AttributeError, OSError, ValueError):
        return None


def _extra_args(content_type: str) -> dict[str, str]:
    return {"ContentType": content_type} if content_type else {}


def _parse_list_objects(body: bytes) -> list[ObjectInfo]:
    root = ET.fromstring(body)
    namespace = ""
    if root.tag.startswith("{"):
        namespace = root.tag.split("}", 1)[0] + "}"
    items: list[ObjectInfo] = []
    for raw in root.findall(f"{namespace}Contents"):
        key = _xml_text(raw, namespace, "Key")
        size = int(_xml_text(raw, namespace, "Size") or 0)
        last_modified_text = _xml_text(raw, namespace, "LastModified")
        last_modified = None
        if last_modified_text:
            try:
                last_modified = datetime.fromisoformat(last_modified_text.replace("Z", "+00:00"))
            except ValueError:
                last_modified = None
        items.append(
            ObjectInfo(
                key=key,
                size=size,
                last_modified=last_modified,
                storage_class=_xml_text(raw, namespace, "StorageClass"),
                etag=_xml_text(raw, namespace, "ETag").strip('"'),
            )
        )
    return items


def _xml_text(element: ET.Element, namespace: str, name: str) -> str:
    found = element.find(f"{namespace}{name}")
    return found.text if found is not None and found.text else ""


def _friendly_http_error(status: int, body: str) -> str:
    low = body.lower()
    if status == 401:
        return "Авторизация не прошла: IAM token недействителен или истёк"
    if status == 403:
        return "Нет доступа к Object Storage или bucket. Проверьте роли storage.viewer/storage.editor"
    if status == 404:
        return "Bucket или объект не найден"
    if "signaturedoesnotmatch" in low:
        return "Ошибка подписи S3 SignatureDoesNotMatch. Переключитесь на IAM-режим или проверьте legacy static key"
    if "nosuchbucket" in low:
        return "Bucket не найден"
    if "accessdenied" in low:
        return "Доступ запрещён: недостаточно прав для bucket/object"
    clean = body.strip()
    return f"Object Storage вернул HTTP {status}" + (f": {clean[:300]}" if clean else "")


def _safe_boto_message(exc: Exception, fallback: str) -> str:
    if isinstance(exc, ClientError):
        error = exc.response.get("Error", {})
        code = error.get("Code") or "Unknown"
        message = error.get("Message") or fallback
        if code == "SignatureDoesNotMatch":
            return f"{fallback}: SignatureDoesNotMatch. Legacy static key request signing failed; IAM mode is recommended."
        if code in {"NoSuchBucket", "404"}:
            return f"{fallback}: bucket не найден"
        if code in {"AccessDenied", "403"}:
            return f"{fallback}: доступ запрещён"
        return f"{fallback}: {code}: {message}"
    if isinstance(exc, NoCredentialsError):
        return "Не указаны legacy static access keys"
    return f"{fallback}: {exc}"


def _friendly_upload_exception(exc: Exception) -> str:
    text = str(exc) or exc.__class__.__name__
    low = text.lower()
    if isinstance(exc, (socket.timeout, TimeoutError)) or "timed out" in low or "timeout" in low:
        return "timeout during upload: Object Storage не успел принять файл. Проверьте сеть и повторите загрузку."
    if isinstance(exc, ssl.SSLError) or "_ssl.c" in low or "ssl" in low or "write" in low:
        return "network write error during upload: соединение оборвалось при отправке файла. Повторите загрузку; multipart retry включён."
    if isinstance(exc, ClientError):
        return _safe_boto_message(exc, "object storage upload failed")
    if isinstance(exc, NoCredentialsError):
        return "object storage upload failed: не указаны credentials"
    return f"object storage upload failed: {exc.__class__.__name__}: {text}"


def _safe_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))

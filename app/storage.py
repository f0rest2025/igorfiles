from __future__ import annotations

import io
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from typing import BinaryIO

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

from app.config import AppConfig, AuthMode, ConfigError
from app.diagnostics import get_logger, redact
from app.iam import TokenProvider, create_token_provider
from app.models import ObjectInfo


logger = get_logger(__name__)


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

    def upload_direct(self, file_obj: BinaryIO, object_key: str, content_type: str = "") -> UploadResult:
        return self.backend.upload_direct(file_obj, object_key, content_type)

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

    def upload_direct(self, file_obj: BinaryIO, object_key: str, content_type: str = "") -> UploadResult:
        raise NotImplementedError

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

    def upload_direct(self, file_obj: BinaryIO, object_key: str, content_type: str = "") -> UploadResult:
        self.config.require_ready()
        data = file_obj.read()
        logger.info("direct upload start bucket=%s key=%s size=%s", self.config.bucket, object_key, len(data))
        headers = {"Content-Length": str(len(data))}
        if content_type:
            headers["Content-Type"] = content_type
        _body, response_headers, _status = self._request("PUT", self._object_url(object_key), data=data, headers=headers, expected={200, 201})
        etag = (response_headers.get("ETag") or response_headers.get("etag") or "").strip('"')
        logger.info("direct upload ok bucket=%s key=%s size=%s", self.config.bucket, object_key, len(data))
        return UploadResult(object_key=object_key, size=len(data), etag=etag)

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
            config=BotoConfig(signature_version="s3v4"),
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

    def upload_direct(self, file_obj: BinaryIO, object_key: str, content_type: str = "") -> UploadResult:
        extra_args = {}
        if content_type:
            extra_args["ContentType"] = content_type
        logger.info("legacy direct upload start bucket=%s key=%s", self.config.bucket, object_key)
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
        raise StorageError(f"Не удалось выполнить legacy upload по pre-signed URL: {exc.reason}") from exc


def upload_bytes_via_client(client: YandexStorageClient, object_key: str, data: bytes, content_type: str = "") -> UploadResult:
    return client.upload_direct(io.BytesIO(data), object_key, content_type)


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


def _safe_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))

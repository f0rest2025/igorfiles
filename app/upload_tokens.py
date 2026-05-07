from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(slots=True)
class UploadToken:
    token: str
    object_key: str
    content_type: str
    expected_file_type: str
    max_size_bytes: int
    expires_at: datetime
    used: bool = False


@dataclass(slots=True)
class DownloadToken:
    token: str
    object_key: str
    expires_at: datetime
    used: bool = False


@dataclass(slots=True)
class LegacyBrowserUploadToken:
    token: str
    upload_url: str
    content_type: str
    expected_file_type: str
    expires_at: datetime


class UploadTokenStore:
    def __init__(self) -> None:
        self._tokens: dict[str, UploadToken] = {}

    def create(
        self,
        object_key: str,
        expires_in: int,
        content_type: str = "",
        expected_file_type: str = "",
        max_size_bytes: int = 0,
    ) -> UploadToken:
        self.cleanup()
        token = secrets.token_urlsafe(32)
        record = UploadToken(
            token=token,
            object_key=object_key,
            content_type=content_type,
            expected_file_type=expected_file_type,
            max_size_bytes=max_size_bytes,
            expires_at=datetime.now(UTC) + timedelta(seconds=expires_in),
        )
        self._tokens[token] = record
        return record

    def get(self, token: str) -> UploadToken | None:
        self.cleanup()
        record = self._tokens.get(token)
        if record is None or record.used:
            return None
        if record.expires_at <= datetime.now(UTC):
            self._tokens.pop(token, None)
            return None
        return record

    def mark_used(self, token: str) -> None:
        if token in self._tokens:
            self._tokens[token].used = True

    def cleanup(self) -> None:
        now = datetime.now(UTC)
        expired = [token for token, record in self._tokens.items() if record.expires_at <= now]
        for token in expired:
            self._tokens.pop(token, None)


class DownloadTokenStore:
    def __init__(self) -> None:
        self._tokens: dict[str, DownloadToken] = {}

    def create(self, object_key: str, expires_in: int) -> DownloadToken:
        self.cleanup()
        token = secrets.token_urlsafe(32)
        record = DownloadToken(
            token=token,
            object_key=object_key,
            expires_at=datetime.now(UTC) + timedelta(seconds=expires_in),
        )
        self._tokens[token] = record
        return record

    def get(self, token: str) -> DownloadToken | None:
        self.cleanup()
        record = self._tokens.get(token)
        if record is None or record.used:
            return None
        if record.expires_at <= datetime.now(UTC):
            self._tokens.pop(token, None)
            return None
        return record

    def mark_used(self, token: str) -> None:
        if token in self._tokens:
            self._tokens[token].used = True

    def cleanup(self) -> None:
        now = datetime.now(UTC)
        expired = [token for token, record in self._tokens.items() if record.expires_at <= now]
        for token in expired:
            self._tokens.pop(token, None)


class LegacyBrowserUploadStore:
    def __init__(self) -> None:
        self._tokens: dict[str, LegacyBrowserUploadToken] = {}

    def create(self, upload_url: str, expires_in: int, content_type: str = "", expected_file_type: str = "") -> LegacyBrowserUploadToken:
        self.cleanup()
        token = secrets.token_urlsafe(32)
        record = LegacyBrowserUploadToken(
            token=token,
            upload_url=upload_url,
            content_type=content_type,
            expected_file_type=expected_file_type,
            expires_at=datetime.now(UTC) + timedelta(seconds=expires_in),
        )
        self._tokens[token] = record
        return record

    def get(self, token: str) -> LegacyBrowserUploadToken | None:
        self.cleanup()
        record = self._tokens.get(token)
        if record is None:
            return None
        if record.expires_at <= datetime.now(UTC):
            self._tokens.pop(token, None)
            return None
        return record

    def cleanup(self) -> None:
        now = datetime.now(UTC)
        expired = [token for token, record in self._tokens.items() if record.expires_at <= now]
        for token in expired:
            self._tokens.pop(token, None)

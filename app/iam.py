from __future__ import annotations

import base64
import json
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from app.config import AppConfig, AuthMode, ConfigError, DEFAULT_REGION
from app.diagnostics import get_logger, redact


KZ_IAM_TOKEN_URL = "https://iam.api.yandexcloud.kz/iam/v1/tokens"
IAM_TOKEN_URL_BY_REGION = {
    DEFAULT_REGION: KZ_IAM_TOKEN_URL,
}
YC_CLI_ENDPOINT_BY_REGION = {
    DEFAULT_REGION: "api.yandexcloud.kz:443",
}
TOKEN_REFRESH_MARGIN_SECONDS = 300

logger = get_logger(__name__)


@dataclass(slots=True)
class IamToken:
    value: str
    expires_at: float


class TokenProvider(Protocol):
    def get_token(self) -> str:
        ...


class YcCliTokenProvider:
    def __init__(self, profile: str = "", region: str = DEFAULT_REGION) -> None:
        self.profile = profile.strip()
        self.region = region
        self._cached: IamToken | None = None

    def get_token(self) -> str:
        if self._cached and self._cached.expires_at - TOKEN_REFRESH_MARGIN_SECONDS > time.time():
            return self._cached.value

        cmd = ["yc", "iam", "create-token"]
        if self.profile:
            cmd.extend(["--profile", self.profile])
        yc_endpoint = YC_CLI_ENDPOINT_BY_REGION.get(self.region)
        if yc_endpoint:
            cmd.extend(["--endpoint", yc_endpoint])
        logger.info("auth token acquire via yc cli profile=%s region=%s", self.profile or "default", self.region)
        try:
            result = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=30)
        except FileNotFoundError as exc:
            raise ConfigError("Yandex Cloud CLI не найден. Установите yc CLI или выберите другой способ аутентификации.") from exc
        except subprocess.TimeoutExpired as exc:
            raise ConfigError("Yandex Cloud CLI не успел выдать IAM token за 30 секунд.") from exc

        if result.returncode != 0:
            error = (result.stderr or result.stdout or "").strip()
            logger.warning("yc cli token error: %s", redact(error))
            if "profile" in error.lower() and ("not found" in error.lower() or "unknown" in error.lower()):
                raise ConfigError(f"Yandex CLI profile не найден: {self.profile or 'default'}")
            raise ConfigError(f"Не удалось получить IAM token через Yandex CLI: {error or 'yc вернул ошибку'}")

        token = result.stdout.strip()
        if not token:
            raise ConfigError("Yandex CLI вернул пустой IAM token")
        self._cached = IamToken(token, time.time() + 3600)
        logger.info("auth token acquired via yc cli profile=%s region=%s", self.profile or "default", self.region)
        return token


class ServiceAccountJsonTokenProvider:
    def __init__(self, key_path: str, iam_token_url: str = KZ_IAM_TOKEN_URL) -> None:
        self.key_path = Path(key_path).expanduser()
        self.iam_token_url = iam_token_url
        self._cached: IamToken | None = None

    def get_token(self) -> str:
        if self._cached and self._cached.expires_at - TOKEN_REFRESH_MARGIN_SECONDS > time.time():
            return self._cached.value
        logger.info("auth token acquire via service account json path=%s iam_url=%s", self.key_path, self.iam_token_url)
        jwt = self._create_jwt()
        payload = json.dumps({"jwt": jwt}).encode("utf-8")
        request = urllib.request.Request(
            self.iam_token_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read(800).decode("utf-8", errors="replace")
            logger.warning("service account token http error status=%s body=%s", exc.code, redact(body))
            raise ConfigError(f"Не удалось обменять service account JWT на IAM token: HTTP {exc.code}: {_friendly_body(body)}") from exc
        except urllib.error.URLError as exc:
            raise ConfigError(f"Не удалось подключиться к IAM API для получения token: {exc.reason}") from exc

        data = json.loads(raw)
        token = data.get("iamToken") or data.get("iam_token") or ""
        if not token:
            raise ConfigError("IAM API не вернул iamToken")
        expires_at = _parse_expiry(data.get("expiresAt")) or (time.time() + 3600)
        self._cached = IamToken(token, expires_at)
        logger.info("auth token acquired via service account json path=%s iam_url=%s", self.key_path, self.iam_token_url)
        return token

    def _create_jwt(self) -> str:
        try:
            raw = json.loads(self.key_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ConfigError(f"Service account JSON не найден: {self.key_path}") from exc
        except json.JSONDecodeError as exc:
            raise ConfigError(f"Service account JSON повреждён: {exc}") from exc

        key_id = str(raw.get("id") or "")
        service_account_id = str(raw.get("service_account_id") or "")
        private_key = str(raw.get("private_key") or "")
        if not key_id or not service_account_id or not private_key:
            raise ConfigError("Service account JSON должен содержать id, service_account_id и private_key")

        now = int(time.time())
        header = {"typ": "JWT", "alg": "PS256", "kid": key_id}
        claims = {
            "aud": self.iam_token_url,
            "iss": service_account_id,
            "iat": now,
            "exp": now + 3600,
        }
        signing_input = f"{_b64url_json(header)}.{_b64url_json(claims)}".encode("ascii")
        key = serialization.load_pem_private_key(private_key.encode("utf-8"), password=None)
        signature = key.sign(
            signing_input,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=hashes.SHA256().digest_size),
            hashes.SHA256(),
        )
        return signing_input.decode("ascii") + "." + _b64url(signature)


def create_token_provider(config: AppConfig) -> TokenProvider | None:
    if config.auth_mode == AuthMode.YC_CLI.value:
        return YcCliTokenProvider(config.yc_profile, config.region)
    if config.auth_mode == AuthMode.SERVICE_ACCOUNT_JSON.value:
        return ServiceAccountJsonTokenProvider(config.service_account_key_path, iam_token_url_for_region(config.region))
    return None


def iam_token_url_for_region(region: str) -> str:
    return IAM_TOKEN_URL_BY_REGION.get((region or "").strip(), KZ_IAM_TOKEN_URL)


def _b64url_json(value: dict) -> str:
    return _b64url(json.dumps(value, separators=(",", ":")).encode("utf-8"))


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _parse_expiry(value: str | None) -> float | None:
    if not value:
        return None
    try:
        from datetime import datetime

        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _friendly_body(body: str) -> str:
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return body[:300]
    return str(data.get("message") or data.get("error") or data)[:300]

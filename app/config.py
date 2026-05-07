from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


APP_DIR_NAME = "YandexStorageFileManager"
REGION_ENDPOINTS = {
    "kz1": "https://storage.yandexcloud.kz",
}
DEFAULT_REGION = "kz1"
DEFAULT_ENDPOINT = REGION_ENDPOINTS[DEFAULT_REGION]
DISABLED_STORAGE_HOSTS = {"storage.yandexcloud.net"}


class AuthMode(StrEnum):
    YC_CLI = "yc_cli"
    SERVICE_ACCOUNT_JSON = "service_account_json"
    LEGACY_STATIC = "legacy_static"


@dataclass(slots=True)
class AppConfig:
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

    def __post_init__(self) -> None:
        self.region = normalize_region(self.region)
        self.endpoint = normalize_endpoint(self.endpoint, self.region)

    @classmethod
    def from_mapping(cls, raw: dict[str, Any] | None) -> "AppConfig":
        raw = raw or {}
        region = normalize_region(str(raw.get("region") or DEFAULT_REGION).strip())
        endpoint = normalize_endpoint(str(raw.get("endpoint") or endpoint_for_region(region)).strip(), region)
        auth_mode_raw = raw.get("auth_mode")
        if auth_mode_raw:
            auth_mode = str(auth_mode_raw).strip()
        elif raw.get("access_key_id") or raw.get("secret_key"):
            auth_mode = AuthMode.LEGACY_STATIC.value
        else:
            auth_mode = AuthMode.YC_CLI.value
        if auth_mode not in {mode.value for mode in AuthMode}:
            auth_mode = AuthMode.YC_CLI.value
        return cls(
            version=int(raw.get("version") or 2),
            access_key_id=str(raw.get("access_key_id") or "").strip(),
            secret_key=str(raw.get("secret_key") or ""),
            bucket=str(raw.get("bucket") or "").strip(),
            prefix=str(raw.get("prefix") or "").strip(),
            endpoint=endpoint,
            region=region,
            auth_mode=auth_mode,
            yc_profile=str(raw.get("yc_profile") or "").strip(),
            service_account_key_path=str(raw.get("service_account_key_path") or "").strip(),
            upload_server_bind_host=str(raw.get("upload_server_bind_host") or "127.0.0.1").strip() or "127.0.0.1",
            upload_server_port=_safe_port(raw.get("upload_server_port")),
            public_base_url=str(raw.get("public_base_url") or "http://127.0.0.1:8765").strip() or "http://127.0.0.1:8765",
            debug=bool(raw.get("debug") or False),
        )

    def merged_with(self, update: "AppConfig", preserve_blank_secret: bool = True) -> "AppConfig":
        secret_key = update.secret_key
        if update.auth_mode != AuthMode.LEGACY_STATIC.value:
            secret_key = ""
        elif preserve_blank_secret and not secret_key:
            secret_key = self.secret_key
        return AppConfig(
            version=2,
            access_key_id=update.access_key_id if update.auth_mode == AuthMode.LEGACY_STATIC.value else "",
            secret_key=secret_key,
            bucket=update.bucket,
            prefix=update.prefix,
            endpoint=normalize_endpoint(update.endpoint, update.region),
            region=normalize_region(update.region),
            auth_mode=update.auth_mode or AuthMode.YC_CLI.value,
            yc_profile=update.yc_profile,
            service_account_key_path=update.service_account_key_path,
            upload_server_bind_host=update.upload_server_bind_host or "127.0.0.1",
            upload_server_port=update.upload_server_port or 8765,
            public_base_url=update.public_base_url or f"http://{update.upload_server_bind_host or '127.0.0.1'}:{update.upload_server_port or 8765}",
            debug=update.debug,
        )

    def public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["secret_key"] = ""
        data["has_secret_key"] = bool(self.secret_key)
        data["auth_mode_label"] = auth_mode_label(self.auth_mode)
        return data

    def require_ready(self) -> None:
        missing = [
            name
            for name, value in {
                "Bucket": self.bucket,
                "Endpoint": self.endpoint,
                "Region": self.region,
            }.items()
            if not value
        ]
        if self.auth_mode == AuthMode.LEGACY_STATIC.value:
            if not self.access_key_id:
                missing.append("Access Key ID")
            if not self.secret_key:
                missing.append("Secret Key")
        elif self.auth_mode == AuthMode.SERVICE_ACCOUNT_JSON.value:
            if not self.service_account_key_path:
                missing.append("Service account JSON")
        elif self.auth_mode == AuthMode.YC_CLI.value:
            pass
        else:
            missing.append("Способ аутентификации")
        if missing:
            raise ConfigError("Не заполнены поля подключения: " + ", ".join(missing))
        if self.region != DEFAULT_REGION:
            raise ConfigError("Поддерживается только KZ region kz1")
        if is_disabled_storage_endpoint(self.endpoint):
            raise ConfigError("Российский endpoint storage.yandexcloud.net отключён. Используйте https://storage.yandexcloud.kz")

    @property
    def uses_legacy_static_keys(self) -> bool:
        return self.auth_mode == AuthMode.LEGACY_STATIC.value


class ConfigError(RuntimeError):
    pass


def default_config_path() -> Path:
    override = os.environ.get("YOS_MANAGER_CONFIG")
    if override:
        return Path(override).expanduser()

    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return base / APP_DIR_NAME / "config.json"

    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "yandex-storage-file-manager" / "config.json"


def load_config(path: Path | None = None) -> AppConfig:
    path = path or default_config_path()
    if not path.exists():
        return AppConfig()
    try:
        with path.open("r", encoding="utf-8") as fh:
            return AppConfig.from_mapping(json.load(fh))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Не удалось прочитать конфиг {path}: {exc}") from exc


def save_config(config: AppConfig, path: Path | None = None) -> Path:
    path = path or default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(asdict(config), fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def delete_config(path: Path | None = None) -> None:
    path = path or default_config_path()
    try:
        path.unlink()
    except FileNotFoundError:
        return


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 6:
        return "***"
    return f"{value[:3]}***{value[-3:]}"


def endpoint_for_region(region: str) -> str:
    return REGION_ENDPOINTS.get(normalize_region(region), DEFAULT_ENDPOINT)


def normalize_region(region: str) -> str:
    region = (region or "").strip()
    if region in REGION_ENDPOINTS:
        return region
    return DEFAULT_REGION


def normalize_endpoint(endpoint: str, region: str = DEFAULT_REGION) -> str:
    endpoint = (endpoint or "").strip()
    if not endpoint or is_disabled_storage_endpoint(endpoint):
        return endpoint_for_region(region)
    return endpoint


def is_disabled_storage_endpoint(endpoint: str) -> bool:
    parsed = urlparse((endpoint or "").strip())
    host = parsed.hostname or (endpoint or "").strip().split("/", 1)[0]
    return host.lower() in DISABLED_STORAGE_HOSTS


def auth_mode_label(auth_mode: str) -> str:
    labels = {
        AuthMode.YC_CLI.value: "Yandex CLI profile / IAM token",
        AuthMode.SERVICE_ACCOUNT_JSON.value: "Service account JSON / IAM token",
        AuthMode.LEGACY_STATIC.value: "Legacy static access key",
    }
    return labels.get(auth_mode, auth_mode)


def _safe_port(value: Any) -> int:
    try:
        port = int(value or 8765)
    except (TypeError, ValueError):
        return 8765
    if port < 1 or port > 65535:
        return 8765
    return port

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


APP_DIR_NAME = "YandexStorageFileManager"
DEFAULT_ENDPOINT = "https://storage.yandexcloud.net"
DEFAULT_REGION = "ru-central1"


@dataclass(slots=True)
class AppConfig:
    access_key_id: str = ""
    secret_key: str = ""
    bucket: str = ""
    prefix: str = ""
    endpoint: str = DEFAULT_ENDPOINT
    region: str = DEFAULT_REGION

    @classmethod
    def from_mapping(cls, raw: dict[str, Any] | None) -> "AppConfig":
        raw = raw or {}
        return cls(
            access_key_id=str(raw.get("access_key_id") or "").strip(),
            secret_key=str(raw.get("secret_key") or ""),
            bucket=str(raw.get("bucket") or "").strip(),
            prefix=str(raw.get("prefix") or "").strip(),
            endpoint=str(raw.get("endpoint") or DEFAULT_ENDPOINT).strip() or DEFAULT_ENDPOINT,
            region=str(raw.get("region") or DEFAULT_REGION).strip() or DEFAULT_REGION,
        )

    def merged_with(self, update: "AppConfig", preserve_blank_secret: bool = True) -> "AppConfig":
        secret_key = update.secret_key
        if preserve_blank_secret and not secret_key:
            secret_key = self.secret_key
        return AppConfig(
            access_key_id=update.access_key_id,
            secret_key=secret_key,
            bucket=update.bucket,
            prefix=update.prefix,
            endpoint=update.endpoint or DEFAULT_ENDPOINT,
            region=update.region or DEFAULT_REGION,
        )

    def public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["secret_key"] = ""
        data["has_secret_key"] = bool(self.secret_key)
        return data

    def require_ready(self) -> None:
        missing = [
            name
            for name, value in {
                "Access Key ID": self.access_key_id,
                "Secret Key": self.secret_key,
                "Bucket": self.bucket,
                "Endpoint": self.endpoint,
                "Region": self.region,
            }.items()
            if not value
        ]
        if missing:
            raise ConfigError("Не заполнены поля подключения: " + ", ".join(missing))


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


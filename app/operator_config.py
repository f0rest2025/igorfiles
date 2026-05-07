from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.config import AppConfig, AuthMode, ConfigError, default_config_path


OPERATOR_CONFIG_FILE_NAME = "desktop_config.json"


def operator_config_path() -> Path:
    return default_config_path().with_name(OPERATOR_CONFIG_FILE_NAME)


def load_operator_config(path: Path | None = None) -> AppConfig:
    path = path or operator_config_path()
    if not path.exists():
        return AppConfig()
    try:
        with path.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Не удалось прочитать operator config {path}: {exc}") from exc
    raw.pop("secret_key", None)
    raw.pop("secret_key_encrypted", None)
    return AppConfig.from_mapping(raw)


def save_operator_config(config: AppConfig, path: Path | None = None) -> Path:
    path = path or operator_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    raw: dict[str, Any] = asdict(config)
    raw["version"] = 3
    raw.pop("secret_key", None)
    if config.auth_mode != AuthMode.LEGACY_STATIC.value:
        raw["access_key_id"] = ""
    with path.open("w", encoding="utf-8") as fh:
        json.dump(raw, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def delete_operator_config(path: Path | None = None) -> None:
    path = path or operator_config_path()
    try:
        path.unlink()
    except FileNotFoundError:
        return


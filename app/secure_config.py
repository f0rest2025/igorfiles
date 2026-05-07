from __future__ import annotations

import base64
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from app.auth import AuthSession
from app.config import AppConfig, ConfigError, DEFAULT_ENDPOINT, DEFAULT_REGION, default_config_path


SECURE_CONFIG_FILE_NAME = "desktop_config.secure.json"


def secure_config_path() -> Path:
    return default_config_path().with_name(SECURE_CONFIG_FILE_NAME)


def load_secure_config(session: AuthSession, path: Path | None = None) -> AppConfig:
    path = path or secure_config_path()
    if not path.exists():
        return AppConfig()
    try:
        with path.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Не удалось прочитать desktop-конфиг {path}: {exc}") from exc

    secret_key = ""
    encrypted_secret = str(raw.get("secret_key_encrypted") or "")
    if encrypted_secret:
        try:
            secret_key = _fernet(session).decrypt(encrypted_secret.encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:
            raise ConfigError("Не удалось расшифровать Secret Key. Проверьте пароль входа.") from exc

    return AppConfig(
        access_key_id=str(raw.get("access_key_id") or "").strip(),
        secret_key=secret_key,
        bucket=str(raw.get("bucket") or "").strip(),
        prefix=str(raw.get("prefix") or "").strip(),
        endpoint=str(raw.get("endpoint") or DEFAULT_ENDPOINT).strip() or DEFAULT_ENDPOINT,
        region=str(raw.get("region") or DEFAULT_REGION).strip() or DEFAULT_REGION,
    )


def save_secure_config(config: AppConfig, session: AuthSession, path: Path | None = None) -> Path:
    path = path or secure_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    raw: dict[str, Any] = asdict(config)
    raw["version"] = 1
    raw["secret_key_encrypted"] = ""
    if config.secret_key:
        raw["secret_key_encrypted"] = _fernet(session).encrypt(config.secret_key.encode("utf-8")).decode("utf-8")
    raw.pop("secret_key", None)

    with path.open("w", encoding="utf-8") as fh:
        json.dump(raw, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def delete_secure_config(path: Path | None = None) -> None:
    path = path or secure_config_path()
    try:
        path.unlink()
    except FileNotFoundError:
        return


def _fernet(session: AuthSession) -> Fernet:
    salt = base64.b64decode(session.encryption_salt.encode("ascii"))
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=session.iterations,
    )
    key = base64.urlsafe_b64encode(kdf.derive(session.password.encode("utf-8")))
    return Fernet(key)


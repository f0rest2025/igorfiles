from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import ConfigError, default_config_path


AUTH_FILE_NAME = "auth.json"
DEFAULT_ITERATIONS = 390_000


@dataclass(slots=True)
class AuthSession:
    username: str
    password: str
    encryption_salt: str
    iterations: int


class AuthManager:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_config_path().with_name(AUTH_FILE_NAME)

    def has_user(self) -> bool:
        return self.path.exists()

    def create_user(self, username: str, password: str) -> AuthSession:
        username = _clean_username(username)
        validate_password(password)
        if self.path.exists():
            raise ConfigError("Пользователь уже создан")

        salt = secrets.token_bytes(32)
        encryption_salt = secrets.token_bytes(32)
        record = {
            "version": 1,
            "username": username,
            "iterations": DEFAULT_ITERATIONS,
            "salt": _b64(salt),
            "password_hash": _b64(_hash_password(password, salt, DEFAULT_ITERATIONS)),
            "encryption_salt": _b64(encryption_salt),
        }
        self._write(record)
        return AuthSession(username=username, password=password, encryption_salt=record["encryption_salt"], iterations=DEFAULT_ITERATIONS)

    def verify(self, username: str, password: str) -> AuthSession:
        record = self._read()
        expected_username = str(record.get("username") or "")
        if _clean_username(username) != expected_username:
            raise ConfigError("Неверный логин или пароль")

        iterations = int(record.get("iterations") or DEFAULT_ITERATIONS)
        salt = _unb64(str(record.get("salt") or ""))
        expected_hash = _unb64(str(record.get("password_hash") or ""))
        actual_hash = _hash_password(password, salt, iterations)
        if not hmac.compare_digest(actual_hash, expected_hash):
            raise ConfigError("Неверный логин или пароль")

        return AuthSession(
            username=expected_username,
            password=password,
            encryption_salt=str(record.get("encryption_salt") or ""),
            iterations=iterations,
        )

    def change_password(self, session: AuthSession, new_password: str) -> AuthSession:
        validate_password(new_password)
        record = self._read()
        if str(record.get("username") or "") != session.username:
            raise ConfigError("Сессия не совпадает с текущим пользователем")

        salt = secrets.token_bytes(32)
        record["salt"] = _b64(salt)
        record["password_hash"] = _b64(_hash_password(new_password, salt, DEFAULT_ITERATIONS))
        record["iterations"] = DEFAULT_ITERATIONS
        self._write(record)
        return AuthSession(
            username=session.username,
            password=new_password,
            encryption_salt=str(record.get("encryption_salt") or ""),
            iterations=DEFAULT_ITERATIONS,
        )

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            raise ConfigError("Пользователь ещё не создан")
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"Не удалось прочитать файл авторизации {self.path}: {exc}") from exc

    def _write(self, record: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as fh:
            json.dump(record, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        try:
            self.path.chmod(0o600)
        except OSError:
            pass


def validate_password(password: str) -> None:
    if len(password) < 8:
        raise ConfigError("Пароль должен быть не короче 8 символов")
    if password.strip() != password:
        raise ConfigError("Пароль не должен начинаться или заканчиваться пробелом")
    if not any(char.isalpha() for char in password):
        raise ConfigError("Пароль должен содержать букву")
    if not any(char.isdigit() for char in password):
        raise ConfigError("Пароль должен содержать цифру")


def _clean_username(username: str) -> str:
    username = (username or "").strip()
    if not username:
        raise ConfigError("Введите логин")
    return username


def _hash_password(password: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.b64decode(value.encode("ascii"))


import json

import pytest

from app.auth import AuthManager
from app.config import AppConfig, AuthMode, ConfigError
from app.secure_config import load_secure_config, save_secure_config


def test_auth_create_verify_and_rejects_wrong_password(tmp_path):
    manager = AuthManager(tmp_path / "auth.json")
    session = manager.create_user("operator", "Password1")

    assert session.username == "operator"
    assert manager.has_user()

    raw = (tmp_path / "auth.json").read_text(encoding="utf-8")
    assert "Password1" not in raw

    verified = manager.verify("operator", "Password1")
    assert verified.username == "operator"

    with pytest.raises(ConfigError):
        manager.verify("operator", "Wrongpass1")


def test_password_policy_requires_reasonable_password(tmp_path):
    manager = AuthManager(tmp_path / "auth.json")
    with pytest.raises(ConfigError):
        manager.create_user("operator", "short")


def test_secure_config_encrypts_secret_key(tmp_path):
    manager = AuthManager(tmp_path / "auth.json")
    session = manager.create_user("operator", "Password1")
    path = tmp_path / "desktop_config.secure.json"
    config = AppConfig(
        access_key_id="access",
        secret_key="secret-value",
        bucket="bucket",
        prefix="incoming",
        endpoint="https://storage.yandexcloud.net",
        region="ru-central1",
        auth_mode=AuthMode.LEGACY_STATIC.value,
    )

    save_secure_config(config, session, path)
    raw = json.loads(path.read_text(encoding="utf-8"))

    assert "secret_key" not in raw
    assert "secret-value" not in path.read_text(encoding="utf-8")
    assert raw["secret_key_encrypted"]

    loaded = load_secure_config(session, path)
    assert loaded.secret_key == "secret-value"
    assert loaded.bucket == "bucket"

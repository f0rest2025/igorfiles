import json

from app.config import AppConfig, AuthMode
from app.operator_config import load_operator_config, save_operator_config


def test_operator_config_does_not_store_legacy_secret_key(tmp_path):
    path = tmp_path / "desktop_config.json"
    config = AppConfig(
        access_key_id="access",
        secret_key="secret-value",
        bucket="bucket",
        prefix="incoming",
        endpoint="https://storage.yandexcloud.kz",
        region="kz1",
        auth_mode=AuthMode.LEGACY_STATIC.value,
    )

    save_operator_config(config, path)
    raw_text = path.read_text(encoding="utf-8")
    raw = json.loads(raw_text)

    assert "secret_key" not in raw
    assert "secret-value" not in raw_text
    assert raw["access_key_id"] == "access"

    loaded = load_operator_config(path)
    assert loaded.secret_key == ""
    assert loaded.bucket == "bucket"


def test_operator_config_clears_static_key_outside_legacy_mode(tmp_path):
    path = tmp_path / "desktop_config.json"
    config = AppConfig(
        access_key_id="legacy-id",
        bucket="bucket",
        auth_mode=AuthMode.YC_CLI.value,
    )

    save_operator_config(config, path)
    raw = json.loads(path.read_text(encoding="utf-8"))

    assert raw["access_key_id"] == ""

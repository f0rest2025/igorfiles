from app.config import AppConfig, AuthMode, endpoint_for_region


def test_default_region_is_kz_only():
    config = AppConfig.from_mapping({})
    assert config.endpoint == "https://storage.yandexcloud.kz"
    assert config.region == "kz1"
    assert endpoint_for_region("kz1") == "https://storage.yandexcloud.kz"


def test_old_ru_region_and_endpoint_migrate_to_kz():
    config = AppConfig.from_mapping({"region": "ru-central1", "endpoint": "https://storage.yandexcloud.net"})
    assert config.region == "kz1"
    assert config.endpoint == "https://storage.yandexcloud.kz"


def test_default_auth_mode_is_yc_cli_not_static_keys():
    config = AppConfig.from_mapping({})
    assert config.auth_mode == AuthMode.YC_CLI.value
    assert not config.uses_legacy_static_keys


def test_old_static_key_config_migrates_to_legacy_mode():
    config = AppConfig.from_mapping({"access_key_id": "id", "secret_key": "secret"})
    assert config.auth_mode == AuthMode.LEGACY_STATIC.value
    assert config.uses_legacy_static_keys


def test_service_account_json_with_path_is_ready():
    config = AppConfig(
        bucket="igorfiles",
        auth_mode=AuthMode.SERVICE_ACCOUNT_JSON.value,
        service_account_key_path="/tmp/authorized_key.json",
    )

    config.require_ready()

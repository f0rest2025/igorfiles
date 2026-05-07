from app.config import AppConfig, AuthMode, endpoint_for_region


def test_kz_region_sets_kz_endpoint_by_default():
    config = AppConfig.from_mapping({"region": "kz1"})
    assert config.endpoint == "https://storage.yandexcloud.kz"
    assert endpoint_for_region("kz1") == "https://storage.yandexcloud.kz"


def test_default_auth_mode_is_yc_cli_not_static_keys():
    config = AppConfig.from_mapping({})
    assert config.auth_mode == AuthMode.YC_CLI.value
    assert not config.uses_legacy_static_keys


def test_old_static_key_config_migrates_to_legacy_mode():
    config = AppConfig.from_mapping({"access_key_id": "id", "secret_key": "secret"})
    assert config.auth_mode == AuthMode.LEGACY_STATIC.value
    assert config.uses_legacy_static_keys

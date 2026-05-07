from app.config import AppConfig, AuthMode
from app.iam import ServiceAccountJsonTokenProvider, YcCliTokenProvider, create_token_provider, iam_token_url_for_region


def test_kz_region_uses_kz_iam_token_url_for_service_account_json():
    config = AppConfig(
        auth_mode=AuthMode.SERVICE_ACCOUNT_JSON.value,
        region="kz1",
        service_account_key_path="/tmp/key.json",
    )

    provider = create_token_provider(config)

    assert isinstance(provider, ServiceAccountJsonTokenProvider)
    assert provider.iam_token_url == "https://iam.api.yandexcloud.kz/iam/v1/tokens"
    assert iam_token_url_for_region("kz1") == "https://iam.api.yandexcloud.kz/iam/v1/tokens"
    assert iam_token_url_for_region("ru-central1") == "https://iam.api.yandexcloud.kz/iam/v1/tokens"


def test_yc_cli_kz_region_uses_kz_api_endpoint(monkeypatch):
    captured = {}

    class Result:
        returncode = 0
        stdout = "iam-token\n"
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return Result()

    monkeypatch.setattr("app.iam.subprocess.run", fake_run)

    token = YcCliTokenProvider(region="kz1").get_token()

    assert token == "iam-token"
    assert "--endpoint" in captured["cmd"]
    assert "api.yandexcloud.kz:443" in captured["cmd"]

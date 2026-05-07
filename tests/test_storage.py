import io
import ssl
from datetime import datetime

import pytest
from botocore.exceptions import ClientError

from app.config import AppConfig, AuthMode
from app.storage import BOTO_IAM_CONFIG, LegacyStaticStorageBackend, StorageError, TRANSFER_CONFIG, IamHttpStorageBackend, _friendly_upload_exception, _parse_list_objects


class FakeClient:
    def __init__(self):
        self.upload_config = None
        self.upload_extra_args = None
        self.uploaded_size = 0

    def generate_presigned_url(self, operation, Params, ExpiresIn, HttpMethod):
        return f"https://storage.example/{Params['Bucket']}/{Params['Key']}?op={operation}&ttl={ExpiresIn}&method={HttpMethod}"

    def head_bucket(self, Bucket):
        return {}

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return self

    def paginate(self, Bucket, Prefix):
        return [
            {
                "Contents": [
                    {
                        "Key": f"{Prefix}/file.txt",
                        "Size": 12,
                        "LastModified": datetime(2026, 1, 1),
                        "StorageClass": "STANDARD",
                        "ETag": '"abc"',
                    }
                ]
            }
        ]

    def upload_fileobj(self, Fileobj, Bucket, Key, ExtraArgs=None, Config=None, Callback=None):
        data = Fileobj.read()
        self.uploaded_size = len(data)
        self.upload_config = Config
        self.upload_extra_args = ExtraArgs
        if Callback:
            Callback(len(data))

    def head_object(self, Bucket, Key):
        return {"ContentLength": self.uploaded_size, "ETag": '"etag"'}


def configured_client(monkeypatch):
    client = LegacyStaticStorageBackend(
        AppConfig(
            access_key_id="access",
            secret_key="secret",
            bucket="bucket",
            endpoint="https://storage.yandexcloud.kz",
            region="kz1",
            auth_mode=AuthMode.LEGACY_STATIC.value,
        )
    )
    monkeypatch.setattr(client, "_client", lambda: FakeClient())
    return client


def test_presign_upload_uses_put_object(monkeypatch):
    client = configured_client(monkeypatch)
    url = client.presign_upload("incoming/file.txt", 600, "text/plain")
    assert "op=put_object" in url
    assert "ttl=600" in url
    assert "method=PUT" in url


def test_list_objects_maps_response(monkeypatch):
    client = configured_client(monkeypatch)
    objects = client.list_objects("incoming")
    assert objects[0].key == "incoming/file.txt"
    assert objects[0].size == 12
    assert objects[0].etag == "abc"


def test_connection_error_is_safe(monkeypatch):
    client = configured_client(monkeypatch)

    class FailingClient(FakeClient):
        def head_bucket(self, Bucket):
            raise ClientError({"Error": {"Code": "403", "Message": "Forbidden"}}, "HeadBucket")

    monkeypatch.setattr(client, "_client", lambda: FailingClient())
    with pytest.raises(StorageError) as exc_info:
        client.test_connection()
    assert "доступ запрещён" in str(exc_info.value)
    assert "secret" not in str(exc_info.value)


def test_legacy_upload_uses_transfer_config_and_progress(monkeypatch):
    fake = FakeClient()
    client = configured_client(monkeypatch)
    monkeypatch.setattr(client, "_client", lambda: fake)
    progress = []

    result = client.upload_direct(io.BytesIO(b"hello"), "incoming/file.txt", "text/plain", progress_callback=lambda done, total: progress.append((done, total)))

    assert result.size == 5
    assert fake.upload_config is TRANSFER_CONFIG
    assert fake.upload_extra_args == {"ContentType": "text/plain"}
    assert progress[-1] == (5, 5)


def test_iam_upload_uses_boto_transfer_with_bearer_header(monkeypatch):
    fake = FakeClient()
    registered = {}

    class Events:
        def register(self, name, callback):
            registered[name] = callback

    class Meta:
        events = Events()

    fake.meta = Meta()
    captured = {}

    def fake_boto_client(*_args, **kwargs):
        captured["config"] = kwargs["config"]
        return fake

    class TokenProvider:
        def get_token(self):
            return "iam-token"

    monkeypatch.setattr("app.storage.boto3.client", fake_boto_client)
    backend = IamHttpStorageBackend(
        AppConfig(bucket="bucket", auth_mode=AuthMode.SERVICE_ACCOUNT_JSON.value, service_account_key_path="/tmp/key.json"),
        TokenProvider(),
    )

    result = backend.upload_direct(io.BytesIO(b"hello"), "incoming/file.txt")

    assert result.size == 5
    assert fake.upload_config is TRANSFER_CONFIG
    assert captured["config"] is BOTO_IAM_CONFIG
    assert "before-send.s3" in registered

    class Request:
        headers = {}

    request = Request()
    registered["before-send.s3"](request)
    assert request.headers["Authorization"] == "Bearer iam-token"


def test_upload_ssl_write_error_has_clear_message():
    message = _friendly_upload_exception(ssl.SSLError("The operation did not complete (write) (_ssl.c:2427)"))
    assert "network write error during upload" in message


def test_parse_list_objects_xml_from_iam_http_api():
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
  <Contents>
    <Key>incoming/file.txt</Key>
    <LastModified>2026-01-01T12:00:00.000Z</LastModified>
    <ETag>"etag"</ETag>
    <Size>42</Size>
    <StorageClass>STANDARD</StorageClass>
  </Contents>
</ListBucketResult>"""

    objects = _parse_list_objects(xml)

    assert objects[0].key == "incoming/file.txt"
    assert objects[0].size == 42
    assert objects[0].etag == "etag"

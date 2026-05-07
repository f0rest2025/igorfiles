from datetime import datetime

import pytest
from botocore.exceptions import ClientError

from app.config import AppConfig, AuthMode
from app.storage import LegacyStaticStorageBackend, StorageError, _parse_list_objects


class FakeClient:
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

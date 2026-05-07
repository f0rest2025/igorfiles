from app.upload_tokens import LegacyBrowserUploadStore, UploadTokenStore


def test_upload_token_is_single_use_after_mark_used():
    store = UploadTokenStore()
    record = store.create("incoming/file.txt", 600, max_size_bytes=1024)

    assert store.get(record.token) is record

    store.mark_used(record.token)

    assert store.get(record.token) is None


def test_legacy_browser_upload_token_keeps_presigned_url_until_expiry():
    store = LegacyBrowserUploadStore()
    record = store.create("https://storage.example/upload?signature=secret", 600, content_type="text/plain")

    loaded = store.get(record.token)

    assert loaded is record
    assert loaded.upload_url.startswith("https://storage.example/")
    assert loaded.content_type == "text/plain"

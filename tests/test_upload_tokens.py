from app.upload_tokens import UploadTokenStore


def test_upload_token_is_single_use_after_mark_used():
    store = UploadTokenStore()
    record = store.create("incoming/file.txt", 600, max_size_bytes=1024)

    assert store.get(record.token) is record

    store.mark_used(record.token)

    assert store.get(record.token) is None


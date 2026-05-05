import asyncio

import pytest
from pydantic import ValidationError

from app.main import health
from app.models import PresignUploadRequest


def test_health_endpoint_starts():
    response = asyncio.run(health())
    assert response.ok is True
    assert response.message == "Сервис запущен"


def test_presign_upload_validation():
    with pytest.raises(ValidationError):
        PresignUploadRequest(object_name="", expires_in=10)

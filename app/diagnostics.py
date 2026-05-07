from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from app.config import default_config_path


TOKEN_RE = re.compile(r"(t1\.[A-Za-z0-9_.-]{12})[A-Za-z0-9_.-]+")
SECRET_KEYS = {"secret", "secret_key", "private_key", "iam_token", "authorization", "password", "jwt"}


def log_path() -> Path:
    return default_config_path().with_name("app.log")


def setup_logging(debug: bool = False) -> Path:
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.FileHandler(path, encoding="utf-8")],
        force=True,
    )
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("boto3").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    return path


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "***" if str(key).lower() in SECRET_KEYS else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        value = TOKEN_RE.sub(r"\1***", value)
        if len(value) > 80 and any(marker in value.lower() for marker in ("bearer ", "private key", "secret")):
            return value[:12] + "***"
    return value


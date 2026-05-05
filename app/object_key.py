from __future__ import annotations

import re
import unicodedata
import uuid
from pathlib import PurePosixPath


CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
SPACES_RE = re.compile(r"\s+")


def normalize_prefix(prefix: str | None) -> str:
    value = (prefix or "").strip().replace("\\", "/")
    parts = []
    for raw_part in value.split("/"):
        part = raw_part.strip()
        if not part or part in {".", ".."}:
            continue
        parts.append(sanitize_segment(part))
    return "/".join(parts)


def sanitize_segment(value: str) -> str:
    value = unicodedata.normalize("NFC", value.strip())
    value = CONTROL_RE.sub("_", value)
    chars: list[str] = []
    for char in value:
        category = unicodedata.category(char)
        if char.isalnum() or char in {" ", ".", "-", "_", "(", ")"}:
            chars.append(char)
        elif category.startswith(("L", "N")):
            chars.append(char)
        else:
            chars.append("_")
    cleaned = "".join(chars)
    cleaned = SPACES_RE.sub(" ", cleaned).strip(" .")
    cleaned = re.sub(r"_+", "_", cleaned)
    return cleaned or "file"


def sanitize_object_name(name: str) -> str:
    value = (name or "").strip().replace("\\", "/")
    parts = []
    for raw_part in value.split("/"):
        part = raw_part.strip()
        if not part or part in {".", ".."}:
            continue
        parts.append(sanitize_segment(part))
    return "/".join(parts) or "file"


def add_guid_to_name(name: str, guid: str | None = None) -> str:
    guid = guid or uuid.uuid4().hex
    path = PurePosixPath(name)
    filename = path.name or "file"
    parent = "" if str(path.parent) == "." else str(path.parent)

    if "." in filename and not filename.startswith("."):
        stem, suffix = filename.rsplit(".", 1)
        filename = f"{stem}-{guid}.{suffix}"
    else:
        filename = f"{filename}-{guid}"

    return f"{parent}/{filename}" if parent else filename


def build_object_key(
    object_name: str,
    prefix: str | None = "",
    *,
    add_guid: bool = False,
    sanitize: bool = True,
) -> str:
    cleaned_prefix = normalize_prefix(prefix)
    name = sanitize_object_name(object_name) if sanitize else (object_name or "").strip().replace("\\", "/").lstrip("/")
    if not name:
        name = "file"
    if add_guid:
        name = add_guid_to_name(name)
    return "/".join(part for part in [cleaned_prefix, name] if part)


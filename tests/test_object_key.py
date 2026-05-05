from app.object_key import add_guid_to_name, build_object_key, normalize_prefix, sanitize_object_name


def test_normalize_prefix_removes_empty_and_unsafe_parts():
    assert normalize_prefix(" /incoming//../Клиенты: 2026/ ") == "incoming/Клиенты_ 2026"


def test_sanitize_keeps_cyrillic_spaces_and_extension():
    assert sanitize_object_name(" договор №1 / акт?.pdf ") == "договор _1/акт_.pdf"


def test_add_guid_before_extension():
    assert add_guid_to_name("incoming/file.pdf", guid="abc") == "incoming/file-abc.pdf"


def test_build_object_key_with_guid_and_prefix():
    key = build_object_key("отчёт?.xlsx", "clients", add_guid=True, sanitize=True)
    assert key.startswith("clients/отчёт_")
    assert key.endswith(".xlsx")


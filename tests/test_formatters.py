"""Tests for formatter functions."""

import pytest
from datetime import datetime
from telegram_mcp.formatters import json_serializer


def test_json_serializer_datetime():
    dt = datetime(2024, 1, 15, 10, 30, 45)
    result = json_serializer(dt)
    assert result == "2024-01-15T10:30:45"


def test_json_serializer_bytes():
    data = b"hello world"
    result = json_serializer(data)
    assert result == "hello world"


def test_json_serializer_bytes_non_utf8():
    data = bytes([0x80, 0x81, 0x82])
    result = json_serializer(data)
    # errors="replace" replaces invalid bytes with replacement character
    assert "\ufffd" in result or "�" in result


def test_json_serializer_unknown_type_raises():
    class UnknownType:
        pass

    obj = UnknownType()
    with pytest.raises(TypeError, match="is not JSON serializable"):
        json_serializer(obj)

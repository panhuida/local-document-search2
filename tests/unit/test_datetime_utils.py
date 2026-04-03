"""UTC aware 时间工具测试。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from local_document_search.utils import (
    aware_utc_from_timestamp,
    format_aware_utc_for_storage,
    normalize_datetime_to_aware_utc,
    parse_datetime_string_to_aware_utc,
    parse_storage_datetime_to_aware_utc,
)


def test_aware_utc_from_timestamp_uses_utc_clock() -> None:
    """验证 POSIX 时间戳会被转换为 aware UTC。"""

    timestamp = 1_712_000_000.0
    expected = datetime.fromtimestamp(timestamp, tz=UTC)
    assert aware_utc_from_timestamp(timestamp) == expected


def test_normalize_datetime_to_aware_utc_accepts_local_naive_input() -> None:
    """验证 naive 输入会按本机本地时间解释后归一化为 aware UTC。"""

    local_naive = datetime(2026, 3, 31, 20, 15, 0)
    expected = local_naive.astimezone(UTC)
    assert normalize_datetime_to_aware_utc(local_naive) == expected


def test_parse_datetime_string_to_aware_utc_preserves_offset_information() -> None:
    """验证带时区偏移的 ISO 字符串会正确转换到 aware UTC。"""

    parsed = parse_datetime_string_to_aware_utc("2026-03-31T20:15:00+08:00")
    assert parsed == datetime(2026, 3, 31, 12, 15, 0, tzinfo=UTC)


def test_storage_round_trip_keeps_aware_utc_semantics() -> None:
    """验证 SQLite 存储字符串与解析结果保持 aware UTC。"""

    original = datetime(2026, 3, 31, 12, 15, 0, 123456, tzinfo=UTC)
    stored = format_aware_utc_for_storage(original)
    assert stored == "2026-03-31T12:15:00.123456Z"
    assert parse_storage_datetime_to_aware_utc(stored) == original


def test_parse_storage_datetime_to_aware_utc_rejects_legacy_naive_string() -> None:
    """验证旧版 SQLite naive 字符串不会再被兼容读取。"""

    with pytest.raises(ValueError, match="不带时区"):
        parse_storage_datetime_to_aware_utc("2026-03-31 12:15:00")

"""统一处理 aware UTC 时间与本地展示格式的工具函数。"""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    """返回当前 UTC aware 时间。"""

    return datetime.now(UTC)


def aware_utc_from_timestamp(timestamp: float) -> datetime:
    """将 POSIX 时间戳转换为 aware UTC datetime。"""

    return datetime.fromtimestamp(timestamp, tz=UTC)


def normalize_datetime_to_aware_utc(value: datetime) -> datetime:
    """将输入时间统一归一化为 aware UTC。

    - 如果传入的是带时区 datetime，则直接转换到 UTC。
    - 如果传入的是 naive datetime，则按本机本地时间解释后再转换到 UTC。
    """

    return value.astimezone(UTC)


def parse_datetime_string_to_aware_utc(raw_value: str | None) -> datetime | None:
    """把 ISO 日期字符串解析并归一化为 aware UTC。"""

    if raw_value is None or raw_value.strip() == "":
        return None
    return normalize_datetime_to_aware_utc(datetime.fromisoformat(raw_value))


def format_aware_utc_for_storage(value: datetime) -> str:
    """把 aware UTC 时间格式化为 SQLite 可稳定排序的 ISO 8601 字符串。"""

    normalized = normalize_datetime_to_aware_utc(value)
    return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_storage_datetime_to_aware_utc(value: datetime | str | None) -> datetime | None:
    """把数据库中的时间值解析为 aware UTC。

    仅接受新方案定义的两类来源：
    - PostgreSQL 驱动返回的 aware datetime
    - SQLite ISO 8601 UTC 字符串：`2026-03-31T12:15:00.000000Z`
    """

    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("数据库中不应出现 naive datetime，请重建或重新索引旧数据。")
        return value.astimezone(UTC)
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("数据库中不应出现不带时区的时间字符串，请重建或重新索引旧数据。")
    return parsed.astimezone(UTC)


def format_datetime_for_local_display(
    value: datetime | None,
    pattern: str = "%Y-%m-%d %H:%M",
) -> str:
    """把 aware UTC 时间转换为本机本地时间字符串，供页面展示。"""

    if value is None:
        return ""
    localized = normalize_datetime_to_aware_utc(value).astimezone()
    return localized.strftime(pattern)

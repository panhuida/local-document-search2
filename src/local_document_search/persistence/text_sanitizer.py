"""数据库文本写入前的清洗工具。"""

from __future__ import annotations


def strip_nul_bytes(value: str) -> tuple[str, bool]:
    """移除字符串中的 NUL 字节，并返回是否发生过清洗。"""

    if "\x00" not in value:
        return value, False
    return value.replace("\x00", ""), True


def strip_nullable_nul_bytes(value: str | None) -> tuple[str | None, bool]:
    """移除可空字符串中的 NUL 字节，并返回是否发生过清洗。"""

    if value is None:
        return None, False
    sanitized_value, changed = strip_nul_bytes(value)
    return sanitized_value, changed

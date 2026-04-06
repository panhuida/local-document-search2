"""搜索后端共享工具。"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from local_document_search.utils import parse_storage_datetime_to_aware_utc

_WHITESPACE_PATTERN = re.compile(r"\s+")


def split_query_terms(query: str) -> tuple[str, ...]:
    """将查询字符串拆分为多个 AND 关键词或双引号短语。"""

    terms: list[str] = []
    buffer: list[str] = []
    in_phrase = False

    for character in query.strip():
        if character == '"':
            if in_phrase:
                _flush_phrase_buffer(buffer, terms)
                in_phrase = False
            else:
                _flush_unquoted_buffer(buffer, terms)
                in_phrase = True
            continue

        buffer.append(character)

    if in_phrase:
        _flush_phrase_buffer(buffer, terms)
    else:
        _flush_unquoted_buffer(buffer, terms)

    return tuple(terms)


def _flush_unquoted_buffer(buffer: list[str], terms: list[str]) -> None:
    """把普通片段按空白拆成多个关键词。"""

    normalized = _normalize_query_fragment("".join(buffer))
    buffer.clear()
    if normalized == "":
        return

    terms.extend(fragment for fragment in normalized.split(" ") if fragment != "")


def _flush_phrase_buffer(buffer: list[str], terms: list[str]) -> None:
    """把双引号中的片段作为一个短语关键词写入结果。"""

    normalized = _normalize_query_fragment("".join(buffer))
    buffer.clear()
    if normalized != "":
        terms.append(normalized)


def _normalize_query_fragment(fragment: str) -> str:
    """压缩片段内部连续空白，避免无意义的空 token。"""

    return _WHITESPACE_PATTERN.sub(" ", fragment).strip()


def make_snippet(content: str, window: int = 160) -> str:
    """在缺少高亮片段时生成简短预览文本。"""

    normalized = re.sub(r"\s+", " ", content).strip()
    if len(normalized) <= window:
        return normalized
    return f"{normalized[:window].rstrip()} ..."


def coerce_search_datetime(value: object, *, logger: logging.Logger) -> datetime | None:
    """兼容多种数据库返回值，统一转换为 aware UTC datetime。"""

    if value is None:
        return None
    try:
        return parse_storage_datetime_to_aware_utc(
            value if isinstance(value, datetime | str) else None
        )
    except ValueError:
        if isinstance(value, str):
            logger.warning("无法解析搜索结果中的时间字段：%s", value)
    return None

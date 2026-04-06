"""搜索后端共享工具。"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from local_document_search.utils import parse_storage_datetime_to_aware_utc


def split_query_terms(query: str) -> tuple[str, ...]:
    """将查询字符串拆分为多个 AND 关键词。"""

    return tuple(term for term in re.split(r"\s+", query.strip()) if term)


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

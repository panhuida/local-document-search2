"""PostgreSQL 搜索后端占位实现。"""

from __future__ import annotations

from local_document_search.exceptions import UnsupportedBackendError
from local_document_search.persistence.search.base import (
    SearchBackend,
    SearchBackendRequest,
    SearchBackendResult,
)


class PostgreSQLSearchBackend(SearchBackend):
    """预留 PostgreSQL 检索能力的后端实现。"""

    def search(self, request: SearchBackendRequest) -> SearchBackendResult:
        """当前版本直接提示后端尚未实现。"""
        raise UnsupportedBackendError("当前版本未实现 PostgreSQL 检索后端。")

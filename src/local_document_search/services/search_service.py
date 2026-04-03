"""搜索服务。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from local_document_search.config import DatabaseBackend
from local_document_search.exceptions import UnsupportedBackendError
from local_document_search.persistence.search import (
    PostgreSQLSearchBackend,
    SearchBackend,
    SearchBackendRequest,
    SQLiteSearchBackend,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchRequest:
    """搜索请求。"""

    query: str
    limit: int
    offset: int = 0


@dataclass(frozen=True)
class SearchHit:
    """单条搜索命中结果。"""

    document_id: int
    file_name: str
    file_path: str
    file_type: str | None
    snippet: str
    score: float | None
    modified_at: datetime | None


@dataclass(frozen=True)
class SearchResult:
    """搜索结果集合。"""

    query: str
    total: int
    limit: int
    offset: int
    hits: tuple[SearchHit, ...]


class SearchService:
    """根据配置选择后端并对外提供统一搜索接口。"""

    def __init__(self, session_factory: sessionmaker[Session], backend: DatabaseBackend) -> None:
        self._backend = self._build_backend(session_factory, backend)

    def search(self, request: SearchRequest) -> SearchResult:
        """执行全文检索，并将后端结果转换为 Service 层模型。"""
        backend_result = self._backend.search(
            SearchBackendRequest(query=request.query, limit=request.limit, offset=request.offset)
        )
        return SearchResult(
            query=request.query,
            total=backend_result.total,
            limit=request.limit,
            offset=request.offset,
            hits=tuple(
                SearchHit(
                    document_id=hit.document_id,
                    file_name=hit.file_name,
                    file_path=hit.file_path,
                    file_type=hit.file_type,
                    snippet=hit.snippet,
                    score=hit.score,
                    modified_at=hit.modified_at,
                )
                for hit in backend_result.hits
            ),
        )

    def _build_backend(
        self,
        session_factory: sessionmaker[Session],
        backend: DatabaseBackend,
    ) -> SearchBackend:
        """按数据库后端配置构造搜索实现。"""
        if backend is DatabaseBackend.SQLITE:
            return SQLiteSearchBackend(session_factory)
        if backend is DatabaseBackend.POSTGRESQL:
            return PostgreSQLSearchBackend()
        raise UnsupportedBackendError(f"未知数据库后端：{backend}")

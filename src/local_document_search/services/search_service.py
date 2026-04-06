"""搜索服务。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from local_document_search.config import DatabaseBackend, SearchMode
from local_document_search.exceptions import UnsupportedBackendError
from local_document_search.persistence.search import (
    PostgreSQLPGroongaSearchBackend,
    PostgreSQLTrigramSearchBackend,
    SearchBackend,
    SearchBackendRequest,
    SQLiteSearchBackend,
)


@dataclass(frozen=True)
class SearchRequest:
    """搜索请求。"""

    query: str
    limit: int
    offset: int = 0
    mode: SearchMode | None = None


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
    mode: SearchMode
    total: int
    limit: int
    offset: int
    hits: tuple[SearchHit, ...]


class SearchService:
    """根据配置选择后端并对外提供统一搜索接口。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        backend: DatabaseBackend,
        postgresql_default_search_mode: SearchMode,
    ) -> None:
        self._backend = backend
        self._postgresql_default_search_mode = postgresql_default_search_mode
        self._sqlite_backend = SQLiteSearchBackend(session_factory)
        self._postgresql_fulltext_backend = PostgreSQLPGroongaSearchBackend(session_factory)
        self._postgresql_fuzzy_backend = PostgreSQLTrigramSearchBackend(session_factory)

    def search(self, request: SearchRequest) -> SearchResult:
        """按请求模式执行检索，并将后端结果转换为 Service 层模型。"""

        resolved_mode = self._resolve_mode(request.mode)
        backend_result = self._select_backend(resolved_mode).search(
            SearchBackendRequest(query=request.query, limit=request.limit, offset=request.offset)
        )
        return SearchResult(
            query=request.query,
            mode=resolved_mode,
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

    def _resolve_mode(self, requested_mode: SearchMode | None) -> SearchMode:
        """解析本次搜索应使用的模式。"""

        if self._backend is DatabaseBackend.POSTGRESQL:
            return requested_mode or self._postgresql_default_search_mode
        return SearchMode.FULLTEXT

    def _select_backend(self, mode: SearchMode) -> SearchBackend:
        """按数据库后端与搜索模式选择实际搜索实现。"""

        if self._backend is DatabaseBackend.SQLITE:
            return self._sqlite_backend
        if self._backend is DatabaseBackend.POSTGRESQL:
            if mode is SearchMode.FULLTEXT:
                return self._postgresql_fulltext_backend
            if mode is SearchMode.FUZZY:
                return self._postgresql_fuzzy_backend
            raise UnsupportedBackendError(f"未知搜索模式：{mode}")
        raise UnsupportedBackendError(f"未知数据库后端：{self._backend}")

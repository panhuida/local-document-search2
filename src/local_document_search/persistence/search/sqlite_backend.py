"""SQLite FTS5 搜索后端。"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from local_document_search.persistence.search.base import (
    SearchBackend,
    SearchBackendHit,
    SearchBackendRequest,
    SearchBackendResult,
)
from local_document_search.utils import parse_storage_datetime_to_aware_utc

logger = logging.getLogger(__name__)


class SQLiteSearchBackend(SearchBackend):
    """基于 FTS5 与 LIKE 回退的默认检索后端。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def search(self, request: SearchBackendRequest) -> SearchBackendResult:
        """先执行 FTS 检索，再补充 LIKE 回退结果。"""
        terms = self._split_terms(request.query)
        if len(terms) == 0:
            return SearchBackendResult(total=0, hits=tuple())

        fetch_size = request.limit + request.offset + 50
        with self._session_factory() as session:
            fts_hits: list[SearchBackendHit] = []
            fts_total = 0
            try:
                fts_total = self._count_fts_matches(session, terms)
                fts_hits = self._search_fts(session, terms, fetch_size)
            except SQLAlchemyError:
                # 当虚拟表异常或查询失败时，至少保证基础搜索仍可用。
                logger.warning("FTS 查询失败，回退到 LIKE 检索", exc_info=True)

            excluded_ids = tuple(hit.document_id for hit in fts_hits)
            like_total = self._count_like_matches(session, terms, excluded_ids)
            like_hits = self._search_like(session, terms, excluded_ids, fetch_size)

        combined_hits = fts_hits + like_hits
        sliced_hits = tuple(combined_hits[request.offset : request.offset + request.limit])
        return SearchBackendResult(total=fts_total + like_total, hits=sliced_hits)

    def _split_terms(self, query: str) -> tuple[str, ...]:
        """将查询字符串拆分为多个 AND 关键词。"""
        return tuple(term for term in re.split(r"\s+", query.strip()) if term)

    def _build_match_query(self, terms: Sequence[str]) -> str:
        """构造 FTS5 MATCH 所需的 AND 查询表达式。"""
        escaped_terms = [term.replace('"', '""') for term in terms]
        return " AND ".join(f'"{term}"' for term in escaped_terms)

    def _count_fts_matches(self, session: Session, terms: Sequence[str]) -> int:
        """统计 FTS5 命中数量。"""
        statement = text(
            """
            SELECT COUNT(*)
            FROM documents_fts
            JOIN documents d ON d.id = documents_fts.rowid
            WHERE documents_fts MATCH :match_query
              AND d.status = 'completed'
            """
        )
        result = session.execute(
            statement, {"match_query": self._build_match_query(terms)}
        ).scalar_one()
        return int(result)

    def _search_fts(
        self,
        session: Session,
        terms: Sequence[str],
        limit: int,
    ) -> list[SearchBackendHit]:
        """执行 FTS5 查询并返回排序后的命中结果。"""
        statement = text(
            """
            SELECT
                d.id AS document_id,
                d.file_name,
                d.file_path,
                d.file_type,
                d.file_modified_time AS modified_at,
                snippet(documents_fts, 1, '<mark>', '</mark>', ' ... ', 14) AS snippet,
                bm25(documents_fts) AS score
            FROM documents_fts
            JOIN documents d ON d.id = documents_fts.rowid
            WHERE documents_fts MATCH :match_query
              AND d.status = 'completed'
            ORDER BY score ASC
            LIMIT :limit
            """
        )
        rows = session.execute(
            statement,
            {"match_query": self._build_match_query(terms), "limit": limit},
        ).mappings()
        return [self._map_row(row) for row in rows]

    def _count_like_matches(
        self,
        session: Session,
        terms: Sequence[str],
        excluded_ids: tuple[int, ...],
    ) -> int:
        """统计 LIKE 回退命中的数量。"""
        query_sql, parameters = self._build_like_query_sql(terms, excluded_ids, count_only=True)
        result = session.execute(text(query_sql), parameters).scalar_one()
        return int(result)

    def _search_like(
        self,
        session: Session,
        terms: Sequence[str],
        excluded_ids: tuple[int, ...],
        limit: int,
    ) -> list[SearchBackendHit]:
        """执行 LIKE 回退查询。"""
        query_sql, parameters = self._build_like_query_sql(terms, excluded_ids, count_only=False)
        parameters["limit"] = limit
        rows = session.execute(text(query_sql), parameters).mappings()
        return [self._map_row(row) for row in rows]

    def _build_like_query_sql(
        self,
        terms: Sequence[str],
        excluded_ids: tuple[int, ...],
        *,
        count_only: bool,
    ) -> tuple[str, dict[str, object]]:
        """生成 SQLite LIKE 查询 SQL 与绑定参数。"""
        parameters: dict[str, object] = {}
        term_clauses: list[str] = []
        for index, term in enumerate(terms):
            parameter_name = f"term_{index}"
            parameters[parameter_name] = f"%{term.lower()}%"
            term_clauses.append(
                f"(LOWER(d.file_name) LIKE :{parameter_name} "
                f"OR LOWER(COALESCE(d.content_markdown, '')) LIKE :{parameter_name})"
            )

        excluded_clause = ""
        if len(excluded_ids) > 0:
            # 先排除已经由 FTS 命中的文档，避免同一文档重复返回。
            excluded_parameters: list[str] = []
            for index, document_id in enumerate(excluded_ids):
                parameter_name = f"excluded_{index}"
                parameters[parameter_name] = document_id
                excluded_parameters.append(f":{parameter_name}")
            excluded_clause = f" AND d.id NOT IN ({', '.join(excluded_parameters)})"

        if count_only:
            return (
                f"""
                SELECT COUNT(*)
                FROM documents d
                WHERE d.status = 'completed'
                  AND {" AND ".join(term_clauses)}
                  {excluded_clause}
                """,
                parameters,
            )

        return (
            f"""
            SELECT
                d.id AS document_id,
                d.file_name,
                d.file_path,
                d.file_type,
                d.file_modified_time AS modified_at,
                d.content_markdown,
                NULL AS score
            FROM documents d
            WHERE d.status = 'completed'
              AND {" AND ".join(term_clauses)}
              {excluded_clause}
            ORDER BY COALESCE(d.file_modified_time, d.updated_at) DESC
            LIMIT :limit
            """,
            parameters,
        )

    def _map_row(self, row: RowMapping) -> SearchBackendHit:
        """将原始 SQL 行映射为统一的命中对象。"""
        snippet_value = row.get("snippet")
        if isinstance(snippet_value, str) and snippet_value.strip() != "":
            snippet = snippet_value
        else:
            content_markdown = row.get("content_markdown")
            snippet = self._make_snippet(
                content_markdown if isinstance(content_markdown, str) else ""
            )

        score_value = row.get("score")
        modified_at = self._coerce_datetime(row.get("modified_at"))
        numeric_score: float | None = None
        if isinstance(score_value, (float, int)):
            numeric_score = float(score_value)

        document_id_value = row["document_id"]
        if not isinstance(document_id_value, int):
            raise ValueError("搜索结果缺少有效的 document_id。")

        file_name_value = row["file_name"]
        if not isinstance(file_name_value, str):
            raise ValueError("搜索结果缺少有效的 file_name。")

        file_path_value = row["file_path"]
        if not isinstance(file_path_value, str):
            raise ValueError("搜索结果缺少有效的 file_path。")

        return SearchBackendHit(
            document_id=document_id_value,
            file_name=file_name_value,
            file_path=file_path_value,
            file_type=str(row["file_type"]) if row.get("file_type") is not None else None,
            snippet=snippet,
            score=numeric_score,
            modified_at=modified_at,
        )

    def _make_snippet(self, content: str, window: int = 160) -> str:
        """在缺少高亮片段时生成简短预览文本。"""
        normalized = re.sub(r"\s+", " ", content).strip()
        if len(normalized) <= window:
            return normalized
        return f"{normalized[:window].rstrip()} ..."

    def _coerce_datetime(self, value: object) -> datetime | None:
        """兼容 SQLite 原生字符串时间，统一转换为 aware UTC datetime。"""
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

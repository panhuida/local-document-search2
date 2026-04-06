"""PostgreSQL 搜索后端。"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session, sessionmaker

from local_document_search.persistence.search.base import (
    SearchBackend,
    SearchBackendHit,
    SearchBackendRequest,
    SearchBackendResult,
)
from local_document_search.persistence.search.common import (
    coerce_search_datetime,
    make_snippet,
    split_query_terms,
)

logger = logging.getLogger(__name__)

_PGROONGA_INDEX_NAME = "idx_documents_search_pgroonga"
_PGROONGA_WEIGHTS = (5, 1)


class _PostgreSQLSearchBackendBase(SearchBackend):
    """封装 PostgreSQL 搜索后端的公共映射逻辑。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def _map_row(self, row: RowMapping) -> SearchBackendHit:
        """将 PostgreSQL 查询结果映射为统一命中对象。"""

        content_markdown = row.get("content_markdown")
        snippet = make_snippet(content_markdown if isinstance(content_markdown, str) else "")
        score_value = row.get("score")
        modified_at = coerce_search_datetime(row.get("modified_at"), logger=logger)
        numeric_score: float | None = None
        if isinstance(score_value, float | int):
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

    def _coerce_datetime(self, value: object) -> datetime | None:
        """兼容 PostgreSQL 原生时间结果，统一转换为 aware UTC datetime。"""

        return coerce_search_datetime(value, logger=logger)


class PostgreSQLTrigramSearchBackend(_PostgreSQLSearchBackendBase):
    """基于 pg_trgm 与 ILIKE 的 PostgreSQL 检索后端。"""

    def search(self, request: SearchBackendRequest) -> SearchBackendResult:
        """使用 pg_trgm 索引加速的 ILIKE 查询执行搜索。"""

        terms = split_query_terms(request.query)
        if len(terms) == 0:
            return SearchBackendResult(total=0, hits=tuple())

        with self._session_factory() as session:
            total = self._count_matches(session, terms)
            hits = self._search_matches(session, terms, request.limit, request.offset)
        return SearchBackendResult(total=total, hits=tuple(hits))

    def _count_matches(self, session: Session, terms: tuple[str, ...]) -> int:
        """统计 ILIKE 命中数量。"""

        query_sql, parameters = self._build_ilike_query_sql(terms, count_only=True)
        result = session.execute(text(query_sql), parameters).scalar_one()
        return int(result)

    def _search_matches(
        self,
        session: Session,
        terms: tuple[str, ...],
        limit: int,
        offset: int,
    ) -> list[SearchBackendHit]:
        """查询并排序 ILIKE 命中结果。"""

        query_sql, parameters = self._build_ilike_query_sql(terms, count_only=False)
        parameters["limit"] = limit
        parameters["offset"] = offset
        rows = session.execute(text(query_sql), parameters).mappings()
        return [self._map_row(row) for row in rows]

    def _build_ilike_query_sql(
        self,
        terms: tuple[str, ...],
        *,
        count_only: bool,
    ) -> tuple[str, dict[str, object]]:
        """生成 PostgreSQL ILIKE 查询 SQL 与绑定参数。"""

        parameters: dict[str, object] = {}
        term_clauses: list[str] = []
        score_fragments: list[str] = []
        for index, term in enumerate(terms):
            like_parameter_name = f"term_{index}"
            raw_parameter_name = f"raw_term_{index}"
            parameters[like_parameter_name] = f"%{term}%"
            term_clauses.append(
                f"(d.file_name ILIKE :{like_parameter_name} "
                f"OR COALESCE(d.content_markdown, '') ILIKE :{like_parameter_name})"
            )
            if not count_only:
                parameters[raw_parameter_name] = term
                score_fragments.append(
                    f"GREATEST("
                    f"similarity(d.file_name, :{raw_parameter_name}), "
                    f"similarity(COALESCE(d.content_markdown, ''), :{raw_parameter_name})"
                    f")"
                )

        where_clause = " AND ".join(term_clauses)
        if count_only:
            return (
                f"""
                SELECT COUNT(*)
                FROM documents d
                WHERE d.status IN ('completed', 'fallback')
                  AND {where_clause}
                """,
                parameters,
            )

        score_expression = " + ".join(score_fragments) if len(score_fragments) > 0 else "0"
        return (
            f"""
            SELECT
                d.id AS document_id,
                d.file_name,
                d.file_path,
                d.file_type,
                d.file_modified_time AS modified_at,
                d.content_markdown,
                ({score_expression}) AS score
            FROM documents d
            WHERE d.status IN ('completed', 'fallback')
              AND {where_clause}
            ORDER BY score DESC, COALESCE(d.file_modified_time, d.updated_at) DESC
            LIMIT :limit
            OFFSET :offset
            """,
            parameters,
        )


class PostgreSQLPGroongaSearchBackend(_PostgreSQLSearchBackendBase):
    """基于 PGroonga 的 PostgreSQL 全文检索后端。"""

    def search(self, request: SearchBackendRequest) -> SearchBackendResult:
        """使用 PGroonga 全文索引执行搜索。"""

        terms = split_query_terms(request.query)
        if len(terms) == 0:
            return SearchBackendResult(total=0, hits=tuple())

        with self._session_factory() as session:
            total = self._count_matches(session, terms)
            hits = self._search_matches(session, terms, request.limit, request.offset)
        return SearchBackendResult(total=total, hits=tuple(hits))

    def _count_matches(self, session: Session, terms: tuple[str, ...]) -> int:
        """统计 PGroonga 命中数量。"""

        query_sql, parameters = self._build_pgroonga_query_sql(terms, count_only=True)
        result = session.execute(text(query_sql), parameters).scalar_one()
        return int(result)

    def _search_matches(
        self,
        session: Session,
        terms: tuple[str, ...],
        limit: int,
        offset: int,
    ) -> list[SearchBackendHit]:
        """查询并排序 PGroonga 命中结果。"""

        query_sql, parameters = self._build_pgroonga_query_sql(terms, count_only=False)
        parameters["limit"] = limit
        parameters["offset"] = offset
        rows = session.execute(text(query_sql), parameters).mappings()
        return [self._map_row(row) for row in rows]

    def _build_pgroonga_query_sql(
        self,
        terms: tuple[str, ...],
        *,
        count_only: bool,
    ) -> tuple[str, dict[str, object]]:
        """生成 PGroonga 查询 SQL 与绑定参数。"""

        parameters: dict[str, object] = {
            "query": self._build_pgroonga_query(terms),
            "weight_file_name": _PGROONGA_WEIGHTS[0],
            "weight_content": _PGROONGA_WEIGHTS[1],
        }
        weights_expression = "ARRAY[:weight_file_name, :weight_content]"
        condition_expression = (
            "pgroonga_condition("
            ":query, "
            f"{weights_expression}, "
            f"index_name => '{_PGROONGA_INDEX_NAME}'"
            ")"
        )
        where_clause = (
            f"ARRAY[d.file_name::text, COALESCE(d.content_markdown, '')] &@~ {condition_expression}"
        )

        if count_only:
            return (
                f"""
                SELECT COUNT(*)
                FROM documents d
                WHERE d.status IN ('completed', 'fallback')
                  AND {where_clause}
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
                pgroonga_score(tableoid, ctid) AS score
            FROM documents d
            WHERE d.status IN ('completed', 'fallback')
              AND {where_clause}
            ORDER BY score DESC, COALESCE(d.file_modified_time, d.updated_at) DESC
            LIMIT :limit
            OFFSET :offset
            """,
            parameters,
        )

    def _build_pgroonga_query(self, terms: tuple[str, ...]) -> str:
        """把当前项目的 AND 语义转换为 PGroonga 查询语法。"""

        return " ".join(self._quote_pgroonga_term(term) for term in terms)

    def _quote_pgroonga_term(self, term: str) -> str:
        """用双引号包裹关键词，避免特殊字符破坏查询语法。"""

        escaped = term.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'


# 向后兼容旧导入路径，默认指向当前推荐的 PGroonga 实现。
PostgreSQLSearchBackend = PostgreSQLPGroongaSearchBackend

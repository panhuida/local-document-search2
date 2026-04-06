"""搜索查询解析相关单元测试。"""

from __future__ import annotations

from sqlalchemy.orm import sessionmaker

from local_document_search.persistence.search.common import split_query_terms
from local_document_search.persistence.search.postgresql_backend import (
    PostgreSQLPGroongaSearchBackend,
    PostgreSQLTrigramSearchBackend,
)
from local_document_search.persistence.search.sqlite_backend import SQLiteSearchBackend


def test_split_query_terms_supports_double_quoted_phrase() -> None:
    """验证双引号中的内容会作为单个短语 token 保留。"""

    assert split_query_terms('历史 "中国 通史" 文学') == (
        "历史",
        "中国 通史",
        "文学",
    )


def test_split_query_terms_tolerates_unclosed_quotes_and_ignores_empty_phrases() -> None:
    """验证未闭合引号会容错处理，空短语会被忽略。"""

    assert split_query_terms('  历史  ""  "中国 通史  ') == (
        "历史",
        "中国 通史",
    )


def test_sqlite_backend_builds_match_query_for_phrase_tokens() -> None:
    """验证 SQLite FTS 查询会把短语 token 作为整体拼进 MATCH 表达式。"""

    backend = SQLiteSearchBackend(sessionmaker())

    assert backend._build_match_query(("历史 长河", "文明")) == '"历史 长河" AND "文明"'


def test_postgresql_trigram_backend_uses_phrase_as_single_ilike_term() -> None:
    """验证 pg_trgm 模糊匹配会把短语作为连续子串查询。"""

    backend = PostgreSQLTrigramSearchBackend(sessionmaker())

    query_sql, parameters = backend._build_ilike_query_sql(("历史 长河", "文明"), count_only=False)

    assert "ILIKE :term_0" in query_sql
    assert parameters["term_0"] == "%历史 长河%"
    assert parameters["raw_term_0"] == "历史 长河"


def test_postgresql_pgroonga_backend_preserves_phrase_tokens() -> None:
    """验证 PGroonga 查询字符串会保留双引号短语。"""

    backend = PostgreSQLPGroongaSearchBackend(sessionmaker())

    assert backend._build_pgroonga_query(("历史 长河", "文明")) == '"历史 长河" "文明"'

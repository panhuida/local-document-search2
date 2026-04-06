"""PostgreSQL 支持相关单元测试。"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from local_document_search.cli import _format_database_target
from local_document_search.config import (
    AppConfig,
    DatabaseBackend,
    LargeFileIndexMode,
    PostgreSQLSearchBackendType,
)
from local_document_search.persistence import database as database_module
from local_document_search.persistence.search.postgresql_backend import (
    PostgreSQLPGroongaSearchBackend,
    PostgreSQLTrigramSearchBackend,
)
from local_document_search.services.search_service import SearchService


def _make_config(
    *,
    project_root: Path,
    database_backend: DatabaseBackend,
    postgresql_search_backend: PostgreSQLSearchBackendType = (PostgreSQLSearchBackendType.PG_TRGM),
    sqlite_db_path: Path | None = None,
    database_url: str | None = None,
) -> AppConfig:
    """构造测试用配置对象。"""

    return AppConfig(
        database_backend=database_backend,
        postgresql_search_backend=postgresql_search_backend,
        sqlite_db_path=sqlite_db_path or project_root / "sqlite" / "search.db",
        database_url=database_url,
        search_dirs=tuple(),
        excluded_dir_keywords=tuple(),
        flask_host="127.0.0.1",
        flask_port=5000,
        flask_debug=False,
        log_level="INFO",
        markitdown_timeout_seconds=90,
        large_file_threshold_mb=15,
        very_large_file_threshold_mb=50,
        large_file_index_mode=LargeFileIndexMode.METADATA,
        project_root=project_root,
    )


class _FakeConnection:
    """收集执行 SQL 的伪连接对象。"""

    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: object, parameters: dict[str, object] | None = None) -> object:
        del parameters
        self.statements.append(str(statement))
        return object()


class _FakeBeginContext:
    """为伪 Engine 提供 begin() 上下文。"""

    def __init__(self, connection: _FakeConnection) -> None:
        self._connection = connection

    def __enter__(self) -> _FakeConnection:
        return self._connection

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        del exc_type, exc, traceback
        return False


class _FakeEngine:
    """仅用于验证初始化 SQL 的伪 Engine。"""

    def __init__(self, connection: _FakeConnection) -> None:
        self._connection = connection

    def begin(self) -> _FakeBeginContext:
        return _FakeBeginContext(self._connection)


def test_format_database_target_masks_postgresql_password(tmp_path: Path) -> None:
    """验证 PostgreSQL 连接串输出会隐藏密码。"""

    config = _make_config(
        project_root=tmp_path,
        database_backend=DatabaseBackend.POSTGRESQL,
        database_url="postgresql://tester:secret@localhost:5432/local_document_search",
    )

    assert (
        _format_database_target(config)
        == "postgresql://tester:***@localhost:5432/local_document_search"
    )


def test_resolve_database_url_requires_postgresql_database_url(tmp_path: Path) -> None:
    """验证 PostgreSQL 模式下缺少 DATABASE_URL 会报错。"""

    config = _make_config(
        project_root=tmp_path,
        database_backend=DatabaseBackend.POSTGRESQL,
        database_url=None,
    )

    with pytest.raises(database_module.UnsupportedBackendError):
        database_module._resolve_database_url(config)


def test_resolve_database_url_defaults_postgresql_driver_to_psycopg(tmp_path: Path) -> None:
    """验证未显式声明驱动的 PostgreSQL URL 会归一化到 psycopg。"""

    config = _make_config(
        project_root=tmp_path,
        database_backend=DatabaseBackend.POSTGRESQL,
        database_url="postgresql://tester:secret@localhost:5432/local_document_search",
    )

    assert database_module._resolve_database_url(config) == (
        "postgresql+psycopg://tester:secret@localhost:5432/local_document_search"
    )


def test_resolve_database_url_accepts_postgres_alias(tmp_path: Path) -> None:
    """验证 `postgres://` 兼容别名也会归一化到 psycopg。"""

    config = _make_config(
        project_root=tmp_path,
        database_backend=DatabaseBackend.POSTGRESQL,
        database_url="postgres://tester:secret@localhost:5432/local_document_search",
    )

    assert database_module._resolve_database_url(config) == (
        "postgresql+psycopg://tester:secret@localhost:5432/local_document_search"
    )


def test_ensure_runtime_directories_skips_sqlite_path_for_postgresql(tmp_path: Path) -> None:
    """验证 PostgreSQL 模式不会额外创建 SQLite 目录。"""

    project_root = tmp_path / "project"
    sqlite_db_path = tmp_path / "external-sqlite" / "search.db"
    config = _make_config(
        project_root=project_root,
        database_backend=DatabaseBackend.POSTGRESQL,
        sqlite_db_path=sqlite_db_path,
        database_url="postgresql://tester:secret@localhost:5432/local_document_search",
    )

    from local_document_search.config import ensure_runtime_directories

    ensure_runtime_directories(config)

    assert not sqlite_db_path.parent.exists()
    assert (project_root / "logs").exists()
    assert (project_root / "data").exists()


def test_ensure_postgresql_search_objects_creates_pg_trgm_indexes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 PostgreSQL 初始化会创建 pg_trgm 扩展与 trigram 索引。"""

    connection = _FakeConnection()
    engine = _FakeEngine(connection)
    monkeypatch.setattr(database_module, "_postgresql_extension_exists", lambda *_args: False)
    monkeypatch.setattr(database_module, "_postgresql_index_exists", lambda *_args: False)

    created = database_module._ensure_postgresql_search_objects(
        cast(Engine, engine),
        PostgreSQLSearchBackendType.PG_TRGM,
    )

    assert created is True
    executed_sql = "\n".join(connection.statements)
    assert "CREATE EXTENSION IF NOT EXISTS pg_trgm" in executed_sql
    assert "idx_documents_file_name_trgm" in executed_sql
    assert "idx_documents_content_markdown_trgm" in executed_sql
    assert "gin_trgm_ops" in executed_sql


def test_ensure_postgresql_search_objects_creates_pgroonga_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 PostgreSQL 初始化可按配置创建 PGroonga 扩展与全文索引。"""

    connection = _FakeConnection()
    engine = _FakeEngine(connection)
    monkeypatch.setattr(database_module, "_postgresql_extension_exists", lambda *_args: False)
    monkeypatch.setattr(database_module, "_postgresql_index_exists", lambda *_args: False)

    created = database_module._ensure_postgresql_search_objects(
        cast(Engine, engine),
        PostgreSQLSearchBackendType.PGROONGA,
    )

    assert created is True
    executed_sql = "\n".join(connection.statements)
    assert "CREATE EXTENSION IF NOT EXISTS pgroonga" in executed_sql
    assert "idx_documents_search_pgroonga" in executed_sql
    assert "USING pgroonga" in executed_sql
    assert "ARRAY[file_name::text, COALESCE(content_markdown, '')]" in executed_sql


def test_postgresql_trigram_backend_builds_ilike_query_with_similarity_score() -> None:
    """验证 PostgreSQL 搜索后端使用 ILIKE 与 similarity 构造查询。"""

    backend = PostgreSQLTrigramSearchBackend(sessionmaker())

    query_sql, parameters = backend._build_ilike_query_sql(("alpha", "beta"), count_only=False)

    assert "ILIKE :term_0" in query_sql
    assert "ILIKE :term_1" in query_sql
    assert "similarity(d.file_name, :raw_term_0)" in query_sql
    assert "similarity(COALESCE(d.content_markdown, ''), :raw_term_1)" in query_sql
    assert "ORDER BY score DESC" in query_sql
    assert parameters["term_0"] == "%alpha%"
    assert parameters["term_1"] == "%beta%"
    assert parameters["raw_term_0"] == "alpha"
    assert parameters["raw_term_1"] == "beta"


def test_postgresql_trigram_backend_count_query_omits_limit_and_raw_term_parameters() -> None:
    """验证 PostgreSQL 计数查询不会注入分页与评分参数。"""

    backend = PostgreSQLTrigramSearchBackend(sessionmaker())

    query_sql, parameters = backend._build_ilike_query_sql(("alpha",), count_only=True)

    assert "COUNT(*)" in query_sql
    assert "LIMIT :limit" not in query_sql
    assert "OFFSET :offset" not in query_sql
    assert parameters == {"term_0": "%alpha%"}


def test_postgresql_pgroonga_backend_builds_weighted_query() -> None:
    """验证 PGroonga 搜索后端使用全文查询、权重与评分函数。"""

    backend = PostgreSQLPGroongaSearchBackend(sessionmaker())

    query_sql, parameters = backend._build_pgroonga_query_sql(("历史", "文档"), count_only=False)

    assert "pgroonga_condition(" in query_sql
    assert "ARRAY[:weight_file_name, :weight_content]" in query_sql
    assert "pgroonga_score(tableoid, ctid) AS score" in query_sql
    assert "idx_documents_search_pgroonga" in query_sql
    assert parameters["query"] == '"历史" "文档"'
    assert parameters["weight_file_name"] == 5
    assert parameters["weight_content"] == 1


def test_postgresql_pgroonga_backend_quotes_special_characters() -> None:
    """验证 PGroonga 查询会对特殊字符做最小转义。"""

    backend = PostgreSQLPGroongaSearchBackend(sessionmaker())

    query = backend._build_pgroonga_query(('C++"17', r"foo\bar"))

    assert query == r'"C++\"17" "foo\\bar"'


def test_search_service_uses_pgroonga_backend_for_postgresql_when_configured() -> None:
    """验证 SearchService 会按配置切换到 PGroonga 后端。"""

    service = SearchService(
        sessionmaker(),
        DatabaseBackend.POSTGRESQL,
        PostgreSQLSearchBackendType.PGROONGA,
    )

    assert isinstance(service._backend, PostgreSQLPGroongaSearchBackend)


def test_search_service_uses_trigram_backend_for_postgresql_when_configured() -> None:
    """验证 SearchService 会按配置保留 pg_trgm 后端。"""

    service = SearchService(
        sessionmaker(),
        DatabaseBackend.POSTGRESQL,
        PostgreSQLSearchBackendType.PG_TRGM,
    )

    assert isinstance(service._backend, PostgreSQLTrigramSearchBackend)

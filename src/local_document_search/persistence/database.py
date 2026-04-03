"""数据库连接与初始化工具。"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from sqlalchemy import DateTime, String, create_engine, event, text
from sqlalchemy.engine import Connection, Dialect, Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator

from local_document_search.config import AppConfig, DatabaseBackend
from local_document_search.exceptions import DatabaseInitializationError, UnsupportedBackendError
from local_document_search.utils import (
    format_aware_utc_for_storage,
    normalize_datetime_to_aware_utc,
    parse_storage_datetime_to_aware_utc,
)

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """ORM 模型基类。"""


class UTCDateTime(TypeDecorator[object]):
    """跨数据库统一的 UTC aware 时间列类型。

    - SQLite：使用 ISO 8601 UTC 字符串存储，便于直读与排序。
    - PostgreSQL：使用真正的 `timestamptz`。
    """

    impl = String
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> object:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(DateTime(timezone=True))
        return dialect.type_descriptor(String(32))

    def process_bind_param(self, value: object, dialect: Dialect) -> object:
        if value is None:
            return None
        if not isinstance(value, datetime):
            raise ValueError("写入时间字段时必须提供 aware datetime。")
        normalized = normalize_datetime_to_aware_utc(value)
        if dialect.name == "postgresql":
            return normalized
        return format_aware_utc_for_storage(normalized)

    def process_result_value(self, value: object, dialect: Dialect) -> object:
        del dialect
        normalized = parse_storage_datetime_to_aware_utc(
            value if isinstance(value, datetime | str) else None
        )
        if normalized is None:
            return None
        return normalized


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_active_database_url: str | None = None


def _resolve_database_url(config: AppConfig) -> str:
    """根据配置解析当前应使用的数据库连接串。"""
    if config.database_backend is DatabaseBackend.SQLITE:
        return f"sqlite:///{config.sqlite_db_path.as_posix()}"
    if config.database_backend is DatabaseBackend.POSTGRESQL and config.database_url:
        return config.database_url
    raise UnsupportedBackendError("当前版本仅支持 SQLite，PostgreSQL 留待后续版本实现。")


def configure_database(config: AppConfig) -> None:
    """按当前配置初始化 engine 与会话工厂。"""
    global _engine, _session_factory, _active_database_url

    database_url = _resolve_database_url(config)
    if _engine is not None and _active_database_url == database_url:
        return

    if _engine is not None:
        _engine.dispose()

    connect_args: dict[str, object] = {}
    if database_url.startswith("sqlite:///"):
        connect_args["check_same_thread"] = False
        # 遇到短时写锁时先等待，避免 CLI / Web 并发下立即报错。
        connect_args["timeout"] = 30
        Path(config.sqlite_db_path).parent.mkdir(parents=True, exist_ok=True)

    _engine = create_engine(database_url, future=True, connect_args=connect_args)
    if database_url.startswith("sqlite:///"):
        _configure_sqlite_pragmas(_engine)
    _session_factory = sessionmaker(bind=_engine, autoflush=False, autocommit=False, future=True)
    _active_database_url = database_url


def get_engine() -> Engine:
    """返回已初始化的 SQLAlchemy Engine。"""
    if _engine is None:
        raise DatabaseInitializationError("数据库尚未配置，请先调用 configure_database()。")
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """返回已初始化的会话工厂。"""
    if _session_factory is None:
        raise DatabaseInitializationError("数据库会话工厂尚未初始化。")
    return _session_factory


@contextmanager
def session_scope() -> Iterator[Session]:
    """提供带自动提交与回滚的会话上下文。"""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def ensure_database_schema() -> None:
    """创建 ORM 表结构。"""
    engine = get_engine()

    try:
        import local_document_search.models  # noqa: F401

        Base.metadata.create_all(bind=engine)
    except Exception as exc:
        logger.exception("创建数据库表结构失败")
        raise DatabaseInitializationError("创建数据库表结构失败") from exc


def ensure_search_objects() -> bool:
    """确保搜索虚拟表与触发器存在，返回是否新建了搜索对象。"""
    engine = get_engine()

    try:
        if engine.dialect.name == "sqlite":
            return _ensure_sqlite_search_objects(engine)
        return False
    except Exception as exc:
        logger.exception("创建搜索对象失败")
        raise DatabaseInitializationError("创建搜索对象失败") from exc


def rebuild_search_index() -> None:
    """重建搜索索引内容。"""
    engine = get_engine()

    try:
        if engine.dialect.name == "sqlite":
            _rebuild_sqlite_search_index(engine)
    except Exception as exc:
        logger.exception("重建搜索索引失败")
        raise DatabaseInitializationError("重建搜索索引失败") from exc


def initialize_database(*, rebuild_index: bool = True) -> None:
    """初始化数据库结构，并按需重建搜索索引。"""
    ensure_database_schema()
    created_search_objects = ensure_search_objects()
    if rebuild_index or created_search_objects:
        rebuild_search_index()


def _configure_sqlite_pragmas(engine: Engine) -> None:
    """为 SQLite 连接启用更适合本地工具场景的 PRAGMA。"""

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection: sqlite3.Connection, connection_record: object) -> None:
        del connection_record
        cursor = dbapi_connection.cursor()
        try:
            # WAL 允许读写并发更平滑，busy_timeout 避免短时锁冲突直接失败。
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA synchronous=NORMAL;")
            cursor.execute("PRAGMA busy_timeout=30000;")
        finally:
            cursor.close()


def _sqlite_object_exists(connection: Connection, object_type: str, name: str) -> bool:
    """检查 SQLite 对象是否已存在。"""
    result = connection.execute(
        text(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = :object_type AND name = :name
            LIMIT 1
            """
        ),
        {"object_type": object_type, "name": name},
    ).scalar_one_or_none()
    return result is not None


def _ensure_sqlite_search_objects(engine: Engine) -> bool:
    """创建 SQLite FTS5 虚拟表及同步触发器。"""
    statements = [
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
            file_name,
            content_markdown,
            file_path UNINDEXED,
            tokenize='unicode61'
        );
        """,
        """
        CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
            INSERT INTO documents_fts(rowid, file_name, content_markdown, file_path)
            VALUES (new.id, new.file_name, COALESCE(new.content_markdown, ''), new.file_path);
        END;
        """,
        """
        CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN
            DELETE FROM documents_fts WHERE rowid = old.id;
        END;
        """,
        """
        CREATE TRIGGER IF NOT EXISTS documents_au AFTER UPDATE ON documents BEGIN
            DELETE FROM documents_fts WHERE rowid = old.id;
            INSERT INTO documents_fts(rowid, file_name, content_markdown, file_path)
            VALUES (new.id, new.file_name, COALESCE(new.content_markdown, ''), new.file_path);
        END;
        """,
    ]

    with engine.begin() as connection:
        fts_table_exists = _sqlite_object_exists(connection, "table", "documents_fts")
        for statement in statements:
            connection.execute(text(statement))
    return not fts_table_exists


def _rebuild_sqlite_search_index(engine: Engine) -> None:
    """重建 SQLite FTS5 虚拟表内容。"""
    statements = [
        "DELETE FROM documents_fts;",
        """
        INSERT INTO documents_fts(rowid, file_name, content_markdown, file_path)
        SELECT id, file_name, COALESCE(content_markdown, ''), file_path FROM documents;
        """,
    ]

    with engine.begin() as connection:
        for statement in statements:
            # 仅在显式初始化或搜索对象首次创建时重建，避免每次启动都全量扫描。
            connection.execute(text(statement))

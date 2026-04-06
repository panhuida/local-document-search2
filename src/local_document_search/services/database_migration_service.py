"""SQLite 到 PostgreSQL 的数据迁移服务。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from local_document_search.exceptions import ConfigurationError, DatabaseInitializationError
from local_document_search.models import Document, IngestState
from local_document_search.persistence.database import (
    ensure_database_schema_for_engine,
    ensure_search_objects_for_engine,
)
from local_document_search.persistence.text_sanitizer import (
    strip_nul_bytes,
    strip_nullable_nul_bytes,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DatabaseMigrationRequest:
    """SQLite 到 PostgreSQL 的迁移请求。"""

    source_sqlite_path: Path
    target_database_url: str
    include_ingest_state: bool = True
    truncate_target: bool = False


@dataclass(frozen=True)
class DatabaseMigrationResult:
    """SQLite 到 PostgreSQL 的迁移结果。"""

    source_sqlite_path: Path
    target_database_target: str
    source_documents: int
    source_ingest_states: int
    migrated_documents: int
    migrated_ingest_states: int
    target_documents: int
    target_ingest_states: int
    include_ingest_state: bool
    truncated_target: bool
    sanitized_document_records: int = 0
    sanitized_document_fields: int = 0
    sanitized_ingest_state_records: int = 0
    sanitized_ingest_state_fields: int = 0
    status: str = "migrated"


class DatabaseMigrationService:
    """负责将 SQLite 业务数据迁移到 PostgreSQL。"""

    def migrate_sqlite_to_postgresql(
        self,
        request: DatabaseMigrationRequest,
    ) -> DatabaseMigrationResult:
        """执行一次 SQLite 到 PostgreSQL 的数据迁移。"""

        normalized_request = self._normalize_request(request)
        source_engine = create_engine(
            f"sqlite:///{normalized_request.source_sqlite_path.as_posix()}",
            future=True,
            connect_args={"check_same_thread": False, "timeout": 30},
        )
        target_engine = create_engine(
            normalized_request.target_database_url,
            future=True,
            pool_pre_ping=True,
        )

        try:
            ensure_database_schema_for_engine(target_engine)
            ensure_search_objects_for_engine(target_engine)
            result = self._copy_data(normalized_request, source_engine, target_engine)
            self._reset_postgresql_sequences(target_engine, normalized_request.include_ingest_state)
            return result
        except SQLAlchemyError as exc:
            logger.exception("执行 SQLite 到 PostgreSQL 迁移失败")
            raise DatabaseInitializationError("执行 SQLite 到 PostgreSQL 迁移失败。") from exc
        finally:
            source_engine.dispose()
            target_engine.dispose()

    def _normalize_request(self, request: DatabaseMigrationRequest) -> DatabaseMigrationRequest:
        """校验并归一化迁移请求。"""

        source_path = request.source_sqlite_path.resolve()
        if not source_path.exists():
            raise ConfigurationError(f"SQLite 源数据库不存在：{source_path}")
        if not source_path.is_file():
            raise ConfigurationError(f"SQLite 源数据库路径不是文件：{source_path}")

        target_database_url = request.target_database_url.strip()
        if target_database_url == "":
            raise ConfigurationError("必须提供 PostgreSQL 目标连接串。")
        if not target_database_url.startswith("postgresql"):
            raise ConfigurationError("目标数据库必须是 PostgreSQL 连接串。")

        return DatabaseMigrationRequest(
            source_sqlite_path=source_path,
            target_database_url=target_database_url,
            include_ingest_state=request.include_ingest_state,
            truncate_target=request.truncate_target,
        )

    def _copy_data(
        self,
        request: DatabaseMigrationRequest,
        source_engine: Engine,
        target_engine: Engine,
    ) -> DatabaseMigrationResult:
        """把源库数据复制到目标库。"""

        with Session(source_engine) as source_session, Session(target_engine) as target_session:
            try:
                self._prepare_target(
                    target_session,
                    include_ingest_state=request.include_ingest_state,
                    truncate_target=request.truncate_target,
                )
                source_documents = self._load_source_documents(source_session)
                source_ingest_states = (
                    self._load_source_ingest_states(source_session)
                    if request.include_ingest_state
                    else []
                )
                (
                    migrated_documents,
                    sanitized_document_records,
                    sanitized_document_fields,
                ) = self._copy_documents(source_documents, target_session)
                (
                    migrated_ingest_states,
                    sanitized_ingest_state_records,
                    sanitized_ingest_state_fields,
                ) = (
                    self._copy_ingest_states(source_ingest_states, target_session)
                    if request.include_ingest_state
                    else (0, 0, 0)
                )
                target_session.commit()
            except Exception:
                target_session.rollback()
                raise

        with Session(target_engine) as verification_session:
            target_documents = self._count_documents(verification_session)
            target_ingest_states = self._count_ingest_states(verification_session)

        return DatabaseMigrationResult(
            source_sqlite_path=request.source_sqlite_path,
            target_database_target=self._mask_database_url(request.target_database_url),
            source_documents=len(source_documents),
            source_ingest_states=len(source_ingest_states),
            migrated_documents=migrated_documents,
            migrated_ingest_states=migrated_ingest_states,
            target_documents=target_documents,
            target_ingest_states=target_ingest_states,
            include_ingest_state=request.include_ingest_state,
            truncated_target=request.truncate_target,
            sanitized_document_records=sanitized_document_records,
            sanitized_document_fields=sanitized_document_fields,
            sanitized_ingest_state_records=sanitized_ingest_state_records,
            sanitized_ingest_state_fields=sanitized_ingest_state_fields,
        )

    def _prepare_target(
        self,
        target_session: Session,
        *,
        include_ingest_state: bool,
        truncate_target: bool,
    ) -> None:
        """校验目标库是否可写入，并按需清空旧数据。"""

        existing_documents = self._count_documents(target_session)
        existing_ingest_states = self._count_ingest_states(target_session)
        if truncate_target:
            self._truncate_target(
                target_session,
                include_ingest_state=include_ingest_state,
            )
            return

        if existing_documents > 0:
            raise ConfigurationError(
                "目标 PostgreSQL 的 documents 表非空，请先清空，或使用 --truncate-target。"
            )
        if include_ingest_state and existing_ingest_states > 0:
            raise ConfigurationError(
                "目标 PostgreSQL 的 ingest_state 表非空，请先清空，或使用 --truncate-target。"
            )

    def _truncate_target(self, target_session: Session, *, include_ingest_state: bool) -> None:
        """按需清空目标 PostgreSQL 表。"""

        target_session.execute(text("TRUNCATE TABLE documents RESTART IDENTITY"))
        if include_ingest_state:
            target_session.execute(text("TRUNCATE TABLE ingest_state RESTART IDENTITY"))
        target_session.flush()

    def _load_source_documents(self, source_session: Session) -> list[Document]:
        """读取源库中的全部文档记录。"""

        statement = select(Document).order_by(Document.id.asc())
        return list(source_session.scalars(statement))

    def _load_source_ingest_states(self, source_session: Session) -> list[IngestState]:
        """读取源库中的全部索引状态记录。"""

        statement = select(IngestState).order_by(IngestState.id.asc())
        return list(source_session.scalars(statement))

    def _copy_documents(
        self,
        source_documents: list[Document],
        target_session: Session,
    ) -> tuple[int, int, int]:
        """复制文档表数据。"""

        sanitized_records = 0
        sanitized_fields = 0
        for row in source_documents:
            sanitized_row, current_sanitized_fields = self._sanitize_document_row(row)
            if current_sanitized_fields > 0:
                sanitized_records += 1
                sanitized_fields += current_sanitized_fields
            target_session.add(
                Document(
                    id=sanitized_row.id,
                    file_name=sanitized_row.file_name,
                    file_type=sanitized_row.file_type,
                    file_size=sanitized_row.file_size,
                    file_created_at=sanitized_row.file_created_at,
                    file_modified_time=sanitized_row.file_modified_time,
                    file_path=sanitized_row.file_path,
                    content_markdown=sanitized_row.content_markdown,
                    conversion_type=sanitized_row.conversion_type,
                    status=sanitized_row.status,
                    error_message=sanitized_row.error_message,
                    source=sanitized_row.source,
                    source_url=sanitized_row.source_url,
                    created_at=sanitized_row.created_at,
                    updated_at=sanitized_row.updated_at,
                )
            )
        target_session.flush()
        if sanitized_fields > 0:
            logger.warning(
                "迁移 documents 时移除了 NUL 字节：记录 %s 条，字段 %s 个。",
                sanitized_records,
                sanitized_fields,
            )
        return len(source_documents), sanitized_records, sanitized_fields

    def _copy_ingest_states(
        self,
        source_ingest_states: list[IngestState],
        target_session: Session,
    ) -> tuple[int, int, int]:
        """复制索引状态表数据。"""

        sanitized_records = 0
        sanitized_fields = 0
        for row in source_ingest_states:
            sanitized_row, current_sanitized_fields = self._sanitize_ingest_state_row(row)
            if current_sanitized_fields > 0:
                sanitized_records += 1
                sanitized_fields += current_sanitized_fields
            target_session.add(
                IngestState(
                    id=sanitized_row.id,
                    source=sanitized_row.source,
                    scope_key=sanitized_row.scope_key,
                    last_started_at=sanitized_row.last_started_at,
                    last_ended_at=sanitized_row.last_ended_at,
                    last_error_message=sanitized_row.last_error_message,
                    cursor_updated_at=sanitized_row.cursor_updated_at,
                    total_files=sanitized_row.total_files,
                    processed=sanitized_row.processed,
                    skipped=sanitized_row.skipped,
                    errors=sanitized_row.errors,
                    last_status=sanitized_row.last_status,
                    created_at=sanitized_row.created_at,
                    updated_at=sanitized_row.updated_at,
                )
            )
        target_session.flush()
        if sanitized_fields > 0:
            logger.warning(
                "迁移 ingest_state 时移除了 NUL 字节：记录 %s 条，字段 %s 个。",
                sanitized_records,
                sanitized_fields,
            )
        return len(source_ingest_states), sanitized_records, sanitized_fields

    def _count_documents(self, session: Session) -> int:
        """统计文档记录数量。"""

        result = session.execute(select(func.count()).select_from(Document)).scalar_one()
        return int(result)

    def _count_ingest_states(self, session: Session) -> int:
        """统计索引状态记录数量。"""

        result = session.execute(select(func.count()).select_from(IngestState)).scalar_one()
        return int(result)

    def _reset_postgresql_sequences(
        self,
        target_engine: Engine,
        include_ingest_state: bool,
    ) -> None:
        """把 PostgreSQL 自增序列推进到迁移后的最大主键。"""

        document_sequence_sql = """
            SELECT setval(
                pg_get_serial_sequence('documents', 'id'),
                COALESCE((SELECT MAX(id) FROM documents), 1),
                (SELECT COUNT(*) > 0 FROM documents)
            );
        """
        ingest_state_sequence_sql = """
            SELECT setval(
                pg_get_serial_sequence('ingest_state', 'id'),
                COALESCE((SELECT MAX(id) FROM ingest_state), 1),
                (SELECT COUNT(*) > 0 FROM ingest_state)
            );
        """

        with target_engine.begin() as connection:
            connection.execute(text(document_sequence_sql))
            if include_ingest_state:
                connection.execute(text(ingest_state_sequence_sql))

    def _mask_database_url(self, database_url: str) -> str:
        """隐藏连接串中的密码，用于 CLI 与日志输出。"""

        parts = urlsplit(database_url)
        if parts.password is None:
            return database_url

        hostname = parts.hostname or ""
        if parts.port is not None:
            hostname = f"{hostname}:{parts.port}"
        username = parts.username or ""
        netloc = f"{username}:***@{hostname}" if username != "" else hostname
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))

    def _sanitize_document_row(self, row: Document) -> tuple[Document, int]:
        """清洗迁移中的文档记录，移除 PostgreSQL 不接受的 NUL 字节。"""

        file_name, file_name_changed = strip_nul_bytes(row.file_name)
        file_type, file_type_changed = strip_nullable_nul_bytes(row.file_type)
        file_path, file_path_changed = strip_nul_bytes(row.file_path)
        content_markdown, content_markdown_changed = strip_nullable_nul_bytes(row.content_markdown)
        status, status_changed = strip_nul_bytes(row.status)
        error_message, error_message_changed = strip_nullable_nul_bytes(row.error_message)
        source, source_changed = strip_nullable_nul_bytes(row.source)
        source_url, source_url_changed = strip_nullable_nul_bytes(row.source_url)
        sanitized_fields = sum(
            (
                file_name_changed,
                file_type_changed,
                file_path_changed,
                content_markdown_changed,
                status_changed,
                error_message_changed,
                source_changed,
                source_url_changed,
            )
        )
        return (
            Document(
                id=row.id,
                file_name=file_name,
                file_type=file_type,
                file_size=row.file_size,
                file_created_at=row.file_created_at,
                file_modified_time=row.file_modified_time,
                file_path=file_path,
                content_markdown=content_markdown,
                conversion_type=row.conversion_type,
                status=status,
                error_message=error_message,
                source=source,
                source_url=source_url,
                created_at=row.created_at,
                updated_at=row.updated_at,
            ),
            sanitized_fields,
        )

    def _sanitize_ingest_state_row(self, row: IngestState) -> tuple[IngestState, int]:
        """清洗迁移中的索引状态记录，移除 PostgreSQL 不接受的 NUL 字节。"""

        source, source_changed = strip_nul_bytes(row.source)
        scope_key, scope_key_changed = strip_nul_bytes(row.scope_key)
        last_error_message, last_error_message_changed = strip_nullable_nul_bytes(
            row.last_error_message
        )
        last_status, last_status_changed = strip_nullable_nul_bytes(row.last_status)
        sanitized_fields = sum(
            (source_changed, scope_key_changed, last_error_message_changed, last_status_changed)
        )
        return (
            IngestState(
                id=row.id,
                source=source,
                scope_key=scope_key,
                last_started_at=row.last_started_at,
                last_ended_at=row.last_ended_at,
                last_error_message=last_error_message,
                cursor_updated_at=row.cursor_updated_at,
                total_files=row.total_files,
                processed=row.processed,
                skipped=row.skipped,
                errors=row.errors,
                last_status=last_status,
                created_at=row.created_at,
                updated_at=row.updated_at,
            ),
            sanitized_fields,
        )

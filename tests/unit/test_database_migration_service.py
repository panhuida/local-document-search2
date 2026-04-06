"""数据库迁移服务测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from local_document_search.exceptions import ConfigurationError
from local_document_search.models import Document, IngestState
from local_document_search.services import DatabaseMigrationRequest, DatabaseMigrationService


def _create_sqlite_engine(database_path: Path) -> Engine:
    """创建测试用 SQLite 引擎。"""

    return create_engine(
        f"sqlite:///{database_path.as_posix()}",
        future=True,
        connect_args={"check_same_thread": False, "timeout": 30},
    )


def _seed_source_data(engine: Engine) -> None:
    """向源库写入一组可迁移的数据。"""

    with Session(engine) as session:
        session.add(
            Document(
                id=7,
                file_name="sample.md",
                file_type="md",
                file_size=12,
                file_created_at=datetime(2026, 4, 6, 0, 0, tzinfo=UTC),
                file_modified_time=datetime(2026, 4, 6, 1, 0, tzinfo=UTC),
                file_path="D:/docs/sample.md",
                content_markdown="# sample document",
                conversion_type=1,
                status="completed",
                error_message=None,
                source="fs",
                source_url=None,
                created_at=datetime(2026, 4, 6, 2, 0, tzinfo=UTC),
                updated_at=datetime(2026, 4, 6, 3, 0, tzinfo=UTC),
            )
        )
        session.add(
            IngestState(
                id=3,
                source="fs",
                scope_key="D:/docs",
                last_started_at=datetime(2026, 4, 6, 4, 0, tzinfo=UTC),
                last_ended_at=datetime(2026, 4, 6, 5, 0, tzinfo=UTC),
                last_error_message=None,
                cursor_updated_at=datetime(2026, 4, 6, 6, 0, tzinfo=UTC),
                total_files=1,
                processed=1,
                skipped=0,
                errors=0,
                last_status="completed",
                created_at=datetime(2026, 4, 6, 7, 0, tzinfo=UTC),
                updated_at=datetime(2026, 4, 6, 8, 0, tzinfo=UTC),
            )
        )
        session.commit()


def test_database_migration_service_validates_request(tmp_path: Path) -> None:
    """验证迁移请求会校验源库路径与目标连接串。"""

    service = DatabaseMigrationService()
    missing_sqlite_request = DatabaseMigrationRequest(
        source_sqlite_path=tmp_path / "missing.db",
        target_database_url="postgresql://user:pass@localhost:5432/app",
    )
    invalid_target_request = DatabaseMigrationRequest(
        source_sqlite_path=tmp_path / "source.db",
        target_database_url="sqlite:///target.db",
    )
    invalid_target_request.source_sqlite_path.write_bytes(b"sqlite")

    with pytest.raises(ConfigurationError):
        service._normalize_request(missing_sqlite_request)

    with pytest.raises(ConfigurationError):
        service._normalize_request(invalid_target_request)


def test_database_migration_service_copies_documents_and_ingest_state(tmp_path: Path) -> None:
    """验证迁移服务的复制逻辑会保留主键与业务字段。"""

    source_engine = _create_sqlite_engine(tmp_path / "source.db")
    target_engine = _create_sqlite_engine(tmp_path / "target.db")
    from local_document_search.persistence.database import ensure_database_schema_for_engine

    ensure_database_schema_for_engine(source_engine)
    ensure_database_schema_for_engine(target_engine)
    _seed_source_data(source_engine)
    service = DatabaseMigrationService()

    with Session(source_engine) as source_session, Session(target_engine) as target_session:
        documents = service._load_source_documents(source_session)
        ingest_states = service._load_source_ingest_states(source_session)
        migrated_documents, sanitized_document_records, sanitized_document_fields = (
            service._copy_documents(documents, target_session)
        )
        migrated_ingest_states, sanitized_ingest_state_records, sanitized_ingest_state_fields = (
            service._copy_ingest_states(ingest_states, target_session)
        )
        target_session.commit()

    with Session(target_engine) as target_session:
        target_document = target_session.scalars(select(Document)).one()
        target_ingest_state = target_session.scalars(select(IngestState)).one()

    assert migrated_documents == 1
    assert migrated_ingest_states == 1
    assert sanitized_document_records == 0
    assert sanitized_document_fields == 0
    assert sanitized_ingest_state_records == 0
    assert sanitized_ingest_state_fields == 0
    assert target_document.id == 7
    assert target_document.file_path == "D:/docs/sample.md"
    assert target_document.status == "completed"
    assert target_ingest_state.id == 3
    assert target_ingest_state.scope_key == "D:/docs"
    assert target_ingest_state.last_status == "completed"

    source_engine.dispose()
    target_engine.dispose()


def test_database_migration_service_rejects_non_empty_target_without_truncate(
    tmp_path: Path,
) -> None:
    """验证目标库非空时需要显式启用 truncate。"""

    target_engine = _create_sqlite_engine(tmp_path / "target.db")
    from local_document_search.persistence.database import ensure_database_schema_for_engine

    ensure_database_schema_for_engine(target_engine)
    with Session(target_engine) as target_session:
        target_session.add(
            Document(
                id=1,
                file_name="existing.md",
                file_type="md",
                file_size=1,
                file_created_at=datetime(2026, 4, 6, 0, 0, tzinfo=UTC),
                file_modified_time=datetime(2026, 4, 6, 0, 0, tzinfo=UTC),
                file_path="D:/docs/existing.md",
                content_markdown="# existing",
                conversion_type=1,
                status="completed",
                error_message=None,
                source="fs",
                source_url=None,
                created_at=datetime(2026, 4, 6, 0, 0, tzinfo=UTC),
                updated_at=datetime(2026, 4, 6, 0, 0, tzinfo=UTC),
            )
        )
        target_session.commit()

    service = DatabaseMigrationService()
    with Session(target_engine) as target_session:
        with pytest.raises(ConfigurationError):
            service._prepare_target(
                target_session,
                include_ingest_state=True,
                truncate_target=False,
            )

    target_engine.dispose()


def test_database_migration_service_sanitizes_nul_bytes_in_migrated_rows(tmp_path: Path) -> None:
    """验证迁移服务会自动移除 PostgreSQL 不接受的 NUL 字节。"""

    source_engine = _create_sqlite_engine(tmp_path / "source-nul.db")
    target_engine = _create_sqlite_engine(tmp_path / "target-nul.db")
    from local_document_search.persistence.database import ensure_database_schema_for_engine

    ensure_database_schema_for_engine(source_engine)
    ensure_database_schema_for_engine(target_engine)
    with Session(source_engine) as session:
        session.add(
            Document(
                id=9,
                file_name="bad\x00name.md",
                file_type="m\x00d",
                file_size=10,
                file_created_at=datetime(2026, 4, 6, 0, 0, tzinfo=UTC),
                file_modified_time=datetime(2026, 4, 6, 0, 0, tzinfo=UTC),
                file_path="D:/docs/bad\x00name.md",
                content_markdown="# bad\x00document",
                conversion_type=1,
                status="com\x00pleted",
                error_message="error\x00message",
                source="f\x00s",
                source_url="https://exa\x00mple.com",
                created_at=datetime(2026, 4, 6, 0, 0, tzinfo=UTC),
                updated_at=datetime(2026, 4, 6, 0, 0, tzinfo=UTC),
            )
        )
        session.add(
            IngestState(
                id=4,
                source="f\x00s",
                scope_key="D:/docs/\x00scope",
                last_started_at=datetime(2026, 4, 6, 0, 0, tzinfo=UTC),
                last_ended_at=datetime(2026, 4, 6, 0, 0, tzinfo=UTC),
                last_error_message="last\x00error",
                cursor_updated_at=datetime(2026, 4, 6, 0, 0, tzinfo=UTC),
                total_files=1,
                processed=1,
                skipped=0,
                errors=0,
                last_status="completed\x00",
                created_at=datetime(2026, 4, 6, 0, 0, tzinfo=UTC),
                updated_at=datetime(2026, 4, 6, 0, 0, tzinfo=UTC),
            )
        )
        session.commit()

    service = DatabaseMigrationService()
    with Session(source_engine) as source_session, Session(target_engine) as target_session:
        documents = service._load_source_documents(source_session)
        ingest_states = service._load_source_ingest_states(source_session)
        migrated_documents, sanitized_document_records, sanitized_document_fields = (
            service._copy_documents(documents, target_session)
        )
        migrated_ingest_states, sanitized_ingest_state_records, sanitized_ingest_state_fields = (
            service._copy_ingest_states(ingest_states, target_session)
        )
        target_session.commit()

    with Session(target_engine) as target_session:
        target_document = target_session.scalars(select(Document)).one()
        target_ingest_state = target_session.scalars(select(IngestState)).one()

    assert migrated_documents == 1
    assert migrated_ingest_states == 1
    assert sanitized_document_records == 1
    assert sanitized_document_fields == 8
    assert sanitized_ingest_state_records == 1
    assert sanitized_ingest_state_fields == 4
    assert "\x00" not in target_document.file_name
    assert "\x00" not in (target_document.file_type or "")
    assert "\x00" not in target_document.file_path
    assert "\x00" not in (target_document.content_markdown or "")
    assert "\x00" not in target_document.status
    assert "\x00" not in (target_document.error_message or "")
    assert "\x00" not in (target_document.source or "")
    assert "\x00" not in (target_document.source_url or "")
    assert "\x00" not in target_ingest_state.source
    assert "\x00" not in target_ingest_state.scope_key
    assert "\x00" not in (target_ingest_state.last_error_message or "")
    assert "\x00" not in (target_ingest_state.last_status or "")

    source_engine.dispose()
    target_engine.dispose()

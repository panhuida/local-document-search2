"""仓储层文本清洗测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from local_document_search.persistence.database import get_session_factory, initialize_database
from local_document_search.persistence.repositories import (
    DocumentRepository,
    DocumentUpsertInput,
    IngestStateRepository,
    IngestStateSummary,
)


def test_document_repository_strips_nul_bytes_before_writing(
    test_environment: Path,
) -> None:
    """验证文档仓储会在写库前移除 NUL 字节。"""

    del test_environment
    initialize_database()

    with get_session_factory()() as session:
        repository = DocumentRepository(session)
        repository.upsert(
            DocumentUpsertInput(
                file_name="nul\x00doc.md",
                file_type="m\x00d",
                file_size=10,
                file_created_at=datetime(2026, 4, 6, 0, 0, tzinfo=UTC),
                file_modified_time=datetime(2026, 4, 6, 0, 0, tzinfo=UTC),
                file_path="D:/docs/nul\x00doc.md",
                content_markdown="# bad\x00content",
                conversion_type=1,
                status="com\x00pleted",
                error_message="error\x00message",
                source="f\x00s",
                source_url="https://exa\x00mple.com",
            )
        )
        session.commit()

    with get_session_factory()() as session:
        document = DocumentRepository(session).get_by_path("D:/docs/nuldoc.md")

    assert document is not None
    assert "\x00" not in document.file_name
    assert "\x00" not in (document.file_type or "")
    assert "\x00" not in document.file_path
    assert "\x00" not in (document.content_markdown or "")
    assert "\x00" not in document.status
    assert "\x00" not in (document.error_message or "")
    assert "\x00" not in (document.source or "")
    assert "\x00" not in (document.source_url or "")


def test_ingest_state_repository_strips_nul_bytes_before_writing(
    test_environment: Path,
) -> None:
    """验证索引状态仓储会在写库前移除 NUL 字节。"""

    del test_environment
    initialize_database()

    with get_session_factory()() as session:
        repository = IngestStateRepository(session)
        repository.finish_run(
            IngestStateSummary(
                source="f\x00s",
                scope_key="D:/docs/\x00scope",
                started_at=datetime(2026, 4, 6, 0, 0, tzinfo=UTC),
                ended_at=datetime(2026, 4, 6, 0, 5, tzinfo=UTC),
                last_status="com\x00pleted",
                last_error_message="error\x00message",
                cursor_updated_at=datetime(2026, 4, 6, 0, 10, tzinfo=UTC),
                total_files=1,
                processed=1,
                skipped=0,
                errors=0,
            )
        )
        session.commit()

    with get_session_factory()() as session:
        state = IngestStateRepository(session).get_by_source_and_scope("fs", "D:/docs/scope")

    assert state is not None
    assert "\x00" not in state.source
    assert "\x00" not in state.scope_key
    assert "\x00" not in (state.last_status or "")
    assert "\x00" not in (state.last_error_message or "")

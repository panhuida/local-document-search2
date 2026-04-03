"""Service 层集成测试。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic, sleep
from urllib.parse import parse_qs, urlparse

import pytest
from flask.testing import FlaskClient
from sqlalchemy import text

from local_document_search import (
    ServiceContainer,
    bootstrap_runtime,
    build_service_container,
    create_app,
)
from local_document_search.config import load_app_config
from local_document_search.converters import ConverterFactory
from local_document_search.converters.base import ConversionResult, ConversionStatus, ConversionType
from local_document_search.exceptions import IndexCancelledError
from local_document_search.persistence import database as database_module
from local_document_search.persistence.database import get_session_factory, initialize_database
from local_document_search.persistence.repositories import (
    DocumentRepository,
    DocumentUpsertInput,
    IngestStateRepository,
)
from local_document_search.services import (
    CleanRequest,
    DocumentPreviewRequest,
    ErrorListRequest,
    FileOpenRequest,
    IndexProgressEvent,
    IndexRequest,
    IndexResult,
    RetryRequest,
    SearchRequest,
)


def _wait_for_index_task_completion(
    client: FlaskClient,
    task_id: str,
    *,
    timeout_seconds: float = 5.0,
) -> dict[str, object]:
    """轮询后台索引任务，直到结束或超时。"""

    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        response = client.get(f"/index/tasks/{task_id}")
        assert response.status_code == 200
        payload = response.get_json()
        assert isinstance(payload, dict)
        if not bool(payload.get("is_active")):
            return payload
        sleep(0.05)
    raise AssertionError(f"索引任务在 {timeout_seconds} 秒内未完成：{task_id}")


def _insert_document_record(
    *,
    file_path: Path,
    status: str,
    content_markdown: str,
    error_message: str | None,
    conversion_type: ConversionType = ConversionType.DIRECT,
) -> int:
    """向测试数据库写入一条文档记录，并返回主键。"""

    with get_session_factory()() as session:
        repository = DocumentRepository(session)
        document = repository.upsert(
            DocumentUpsertInput(
                file_name=file_path.name,
                file_type=file_path.suffix.lower().lstrip(".") or None,
                file_size=file_path.stat().st_size,
                file_created_at=datetime.fromtimestamp(file_path.stat().st_ctime, tz=UTC),
                file_modified_time=datetime.fromtimestamp(file_path.stat().st_mtime, tz=UTC),
                file_path=str(file_path.resolve()),
                content_markdown=content_markdown,
                conversion_type=int(conversion_type),
                status=status,
                error_message=error_message,
                source="fs",
                source_url=None,
            )
        )
        session.commit()
        return int(document.id)


def test_index_search_and_preview_services(
    test_environment: Path,
    copied_documents_dir: Path,
) -> None:
    """验证索引、搜索与预览服务可以协同工作。"""
    del test_environment
    initialize_database()
    container = build_service_container(force_reload=True)

    index_result = container.index_service.index_documents(
        IndexRequest(
            paths=(copied_documents_dir,),
            recursive=True,
            modified_since=None,
            file_types=tuple(),
            force=False,
        )
    )

    assert index_result.total_files == 3
    assert index_result.processed == 2
    assert index_result.errors == 1

    search_result = container.search_service.search(SearchRequest(query="sample", limit=10))
    assert search_result.total >= 2
    assert any("sample.md" == item.file_name for item in search_result.hits)

    preview_id = next(
        item.document_id for item in index_result.files if item.file_path.endswith("sample.md")
    )
    preview_result = container.document_service.preview_document(
        DocumentPreviewRequest(document_id=preview_id or 0)
    )
    assert "本地文档搜索助手" in preview_result.content_markdown
    assert "<h1" in preview_result.content_html


def test_index_service_persists_file_timestamps_as_aware_utc(
    test_environment: Path,
    copied_documents_dir: Path,
) -> None:
    """验证索引服务写入的文件时间统一为 aware UTC。"""

    del test_environment
    initialize_database()
    container = build_service_container(force_reload=True)
    sample_file = copied_documents_dir / "sample.md"

    container.index_service.index_documents(
        IndexRequest(
            paths=(copied_documents_dir,),
            recursive=True,
            modified_since=None,
            file_types=tuple(),
            force=False,
        )
    )

    expected_created_time = datetime.fromtimestamp(sample_file.stat().st_ctime, tz=UTC)
    expected_modified_time = datetime.fromtimestamp(sample_file.stat().st_mtime, tz=UTC)
    with get_session_factory()() as session:
        document = DocumentRepository(session).get_by_path(str(sample_file.resolve()))

    assert document is not None
    assert document.file_created_at is not None
    assert document.file_modified_time is not None
    assert document.file_created_at == expected_created_time
    assert document.file_modified_time == expected_modified_time
    assert document.file_created_at.tzinfo == UTC
    assert document.file_modified_time.tzinfo == UTC


def test_retry_and_clean_services(
    test_environment: Path,
    copied_documents_dir: Path,
) -> None:
    """验证失败重试与孤儿清理流程。"""
    del test_environment
    initialize_database()
    container = build_service_container(force_reload=True)

    container.index_service.index_documents(
        IndexRequest(
            paths=(copied_documents_dir,),
            recursive=True,
            modified_since=None,
            file_types=tuple(),
            force=False,
        )
    )

    fixed_drawio = copied_documents_dir / "broken.drawio"
    # 将损坏的 draw.io 文件修复后再次重试，确认状态可恢复。
    fixed_drawio.write_text(
        (
            '<mxfile><diagram name="Recovered"><mxGraphModel><root>'
            '<mxCell id="0" value="sample repaired"/></root>'
            "</mxGraphModel></diagram></mxfile>"
        ),
        encoding="utf-8",
    )

    retry_result = container.error_record_service.retry_errors(
        RetryRequest(file_path=str(fixed_drawio.resolve()))
    )
    assert retry_result.succeeded == 1
    assert retry_result.failed == 0

    expected_modified_time = datetime.fromtimestamp(fixed_drawio.stat().st_mtime, tz=UTC)
    expected_created_time = datetime.fromtimestamp(fixed_drawio.stat().st_ctime, tz=UTC)
    with get_session_factory()() as session:
        document = DocumentRepository(session).get_by_path(str(fixed_drawio.resolve()))

    assert document is not None
    assert document.file_created_at is not None
    assert document.file_modified_time is not None
    assert document.file_created_at == expected_created_time
    assert document.file_modified_time == expected_modified_time
    assert document.file_created_at.tzinfo == UTC
    assert document.file_modified_time.tzinfo == UTC

    markdown_file = copied_documents_dir / "sample.md"
    markdown_file.unlink()
    clean_result = container.clean_service.clean_orphans(
        CleanRequest(scope_path=copied_documents_dir, dry_run=False)
    )
    assert clean_result.matched >= 1
    assert clean_result.deleted >= 1


def test_index_service_supports_dry_run_without_writing_database(
    test_environment: Path,
    copied_documents_dir: Path,
) -> None:
    """验证 index dry-run 只预览命中范围，不写入索引库。"""

    del test_environment
    initialize_database()
    container = build_service_container(force_reload=True)

    index_result = container.index_service.index_documents(
        IndexRequest(
            paths=(copied_documents_dir,),
            recursive=True,
            modified_since=None,
            file_types=tuple(),
            force=False,
            dry_run=True,
        )
    )

    assert index_result.dry_run is True
    assert index_result.total_files == 3
    assert index_result.planned == 3
    assert index_result.processed == 0
    assert index_result.errors == 0
    assert all(item.status == "planned" for item in index_result.files)

    search_result = container.search_service.search(SearchRequest(query="sample", limit=10))
    assert search_result.total == 0


def test_retry_service_supports_dry_run_without_updating_failed_record(
    test_environment: Path,
    copied_documents_dir: Path,
) -> None:
    """验证 retry dry-run 只预览目标，不修改失败记录。"""

    del test_environment
    initialize_database()
    container = build_service_container(force_reload=True)

    container.index_service.index_documents(
        IndexRequest(
            paths=(copied_documents_dir,),
            recursive=True,
            modified_since=None,
            file_types=tuple(),
            force=False,
        )
    )

    fixed_drawio = copied_documents_dir / "broken.drawio"
    fixed_drawio.write_text(
        (
            '<mxfile><diagram name="Recovered"><mxGraphModel><root>'
            '<mxCell id="0" value="sample repaired"/></root>'
            "</mxGraphModel></diagram></mxfile>"
        ),
        encoding="utf-8",
    )

    retry_result = container.error_record_service.retry_errors(
        RetryRequest(file_path=str(fixed_drawio.resolve()), dry_run=True)
    )

    assert retry_result.dry_run is True
    assert retry_result.total == 1
    assert retry_result.planned == 1
    assert retry_result.succeeded == 0
    assert retry_result.failed == 0
    assert retry_result.items[0].status == "planned"

    with get_session_factory()() as session:
        document = DocumentRepository(session).get_by_path(str(fixed_drawio.resolve()))

    assert document is not None
    assert document.status == "failed"
    assert document.error_message is not None


def test_search_service_includes_fallback_documents(
    test_environment: Path,
    tmp_path: Path,
) -> None:
    """验证回退为元数据索引的文档仍会出现在搜索结果中。"""

    del test_environment
    initialize_database()
    fallback_file = tmp_path / "fallback.pdf"
    fallback_file.write_bytes(b"%PDF-1.4 fallback")
    _insert_document_record(
        file_path=fallback_file,
        status="fallback",
        content_markdown="# 回退文档\n\n独特关键词 fallback-search-token",
        error_message="转换失败后回退为元数据索引",
        conversion_type=ConversionType.STRUCTURED_TO_MD,
    )

    container = build_service_container(force_reload=True)
    search_result = container.search_service.search(
        SearchRequest(query="fallback-search-token", limit=10)
    )

    assert search_result.total == 1
    assert search_result.hits[0].file_name == fallback_file.name
    assert search_result.hits[0].snippet is not None
    assert "fallback-search-token" in search_result.hits[0].snippet


def test_error_record_service_can_include_fallback_records(
    test_environment: Path,
    tmp_path: Path,
) -> None:
    """验证错误记录服务默认排除回退记录，并可按参数一并查询。"""

    del test_environment
    initialize_database()
    failed_file = tmp_path / "failed.drawio"
    failed_file.write_text("<mxfile><diagram>broken", encoding="utf-8")
    fallback_file = tmp_path / "fallback.pdf"
    fallback_file.write_bytes(b"%PDF-1.4 fallback")
    _insert_document_record(
        file_path=failed_file,
        status="failed",
        content_markdown="# failed",
        error_message="转换失败",
        conversion_type=ConversionType.DRAWIO_TO_MD,
    )
    _insert_document_record(
        file_path=fallback_file,
        status="fallback",
        content_markdown="# fallback",
        error_message="回退为元数据索引",
        conversion_type=ConversionType.STRUCTURED_TO_MD,
    )

    container = build_service_container(force_reload=True)
    default_result = container.error_record_service.list_errors(ErrorListRequest())
    fallback_result = container.error_record_service.list_errors(
        ErrorListRequest(include_fallback=True)
    )

    assert default_result.total == 1
    assert [item.status for item in default_result.items] == ["failed"]
    assert fallback_result.total == 2
    assert {item.status for item in fallback_result.items} == {"failed", "fallback"}


def test_index_service_excludes_assets_directories_by_default(
    test_environment: Path,
    tmp_path: Path,
) -> None:
    """验证默认会跳过名称包含 .assets 的图片目录。"""

    del test_environment
    initialize_database()
    container = build_service_container(force_reload=True)

    docs_dir = tmp_path / "notes"
    assets_dir = docs_dir / "chapter.assets"
    docs_dir.mkdir()
    assets_dir.mkdir()
    (docs_dir / "note.md").write_text("# note", encoding="utf-8")
    (assets_dir / "image.png").write_bytes(b"fake image bytes")

    index_result = container.index_service.index_documents(
        IndexRequest(
            paths=(docs_dir,),
            recursive=True,
            modified_since=None,
            file_types=tuple(),
            force=False,
        )
    )

    assert index_result.total_files == 1
    assert index_result.processed == 1
    assert all(".assets" not in item.file_path for item in index_result.files)


def test_index_service_allows_overriding_excluded_directories(
    test_environment: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证显式清空排除配置后，.assets 目录也会被扫描。"""

    del test_environment
    monkeypatch.setenv("EXCLUDED_DIR_KEYWORDS", "")
    initialize_database()
    container = build_service_container(force_reload=True)

    docs_dir = tmp_path / "notes"
    assets_dir = docs_dir / "chapter.assets"
    docs_dir.mkdir()
    assets_dir.mkdir()
    (docs_dir / "note.md").write_text("# note", encoding="utf-8")
    (assets_dir / "image.png").write_bytes(b"fake image bytes")

    index_result = container.index_service.index_documents(
        IndexRequest(
            paths=(docs_dir,),
            recursive=True,
            modified_since=None,
            file_types=tuple(),
            force=False,
        )
    )

    assert index_result.total_files == 2
    assert any(".assets" in item.file_path for item in index_result.files)


def test_web_index_page_auto_initializes_database(
    test_environment: Path,
    copied_documents_dir: Path,
) -> None:
    """验证 Web 索引页会启动后台任务，并可轮询到最终结果。"""
    del test_environment
    app = create_app()
    app.config["TESTING"] = True

    with app.test_client() as client:
        response = client.post(
            "/index",
            data={
                "path": str(copied_documents_dir),
                "recursive": "on",
                "force": "",
                "file_types": ["md", "html", "drawio"],
            },
            follow_redirects=False,
        )
        assert response.status_code == 302
        redirect_location = response.headers["Location"]
        parsed_location = urlparse(redirect_location)
        task_id = parse_qs(parsed_location.query)["task_id"][0]
        task_payload = _wait_for_index_task_completion(client, task_id)

        page_response = client.get(redirect_location)

    assert page_response.status_code == 200
    page = page_response.get_data(as_text=True)
    assert "目录路径" in page
    assert "sample.md" in page
    assert "任务 ID" in page
    assert task_payload["status"] == "completed"
    assert task_payload["processed"] == 2
    assert task_payload["errors"] == 1


def test_web_force_reindex_repairs_legacy_naive_datetime_rows(
    test_environment: Path,
    copied_documents_dir: Path,
) -> None:
    """验证 Web 强制重建可以覆盖旧版 naive 时间数据，而不是直接报错。"""

    del test_environment
    initialize_database()
    sample_file = copied_documents_dir / "sample.md"
    sample_path = str(sample_file.resolve())
    scope_key = str(copied_documents_dir.resolve())

    with get_session_factory()() as session:
        session.execute(
            text(
                """
                INSERT INTO documents (
                    file_name,
                    file_type,
                    file_size,
                    file_created_at,
                    file_modified_time,
                    file_path,
                    content_markdown,
                    conversion_type,
                    status,
                    error_message,
                    source,
                    source_url,
                    created_at,
                    updated_at
                ) VALUES (
                    :file_name,
                    :file_type,
                    :file_size,
                    :file_created_at,
                    :file_modified_time,
                    :file_path,
                    :content_markdown,
                    :conversion_type,
                    :status,
                    :error_message,
                    :source,
                    :source_url,
                    :created_at,
                    :updated_at
                )
                """
            ),
            {
                "file_name": sample_file.name,
                "file_type": "md",
                "file_size": sample_file.stat().st_size,
                "file_created_at": "2026-03-31T12:00:00",
                "file_modified_time": "2026-03-31T12:00:00",
                "file_path": sample_path,
                "content_markdown": "# legacy",
                "conversion_type": 1,
                "status": "completed",
                "error_message": None,
                "source": "fs",
                "source_url": None,
                "created_at": "2026-03-31T12:00:00",
                "updated_at": "2026-03-31T12:00:00",
            },
        )
        session.execute(
            text(
                """
                INSERT INTO ingest_state (
                    source,
                    scope_key,
                    last_started_at,
                    last_ended_at,
                    last_error_message,
                    cursor_updated_at,
                    total_files,
                    processed,
                    skipped,
                    errors,
                    last_status,
                    created_at,
                    updated_at
                ) VALUES (
                    :source,
                    :scope_key,
                    :last_started_at,
                    :last_ended_at,
                    :last_error_message,
                    :cursor_updated_at,
                    :total_files,
                    :processed,
                    :skipped,
                    :errors,
                    :last_status,
                    :created_at,
                    :updated_at
                )
                """
            ),
            {
                "source": "fs",
                "scope_key": scope_key,
                "last_started_at": "2026-03-31T12:00:00",
                "last_ended_at": "2026-03-31T12:00:00",
                "last_error_message": None,
                "cursor_updated_at": "2026-03-31T12:00:00",
                "total_files": 1,
                "processed": 1,
                "skipped": 0,
                "errors": 0,
                "last_status": "success",
                "created_at": "2026-03-31T12:00:00",
                "updated_at": "2026-03-31T12:00:00",
            },
        )
        session.commit()

    app = create_app()
    app.config["TESTING"] = True

    with app.test_client() as client:
        response = client.post(
            "/index",
            data={
                "path": str(copied_documents_dir),
                "recursive": "on",
                "force": "on",
                "file_types": ["md"],
            },
            follow_redirects=False,
        )
        assert response.status_code == 302
        redirect_location = response.headers["Location"]
        parsed_location = urlparse(redirect_location)
        task_id = parse_qs(parsed_location.query)["task_id"][0]
        task_payload = _wait_for_index_task_completion(client, task_id)

        page_response = client.get(redirect_location)

    assert page_response.status_code == 200
    page = page_response.get_data(as_text=True)
    assert "数据库中不应出现不带时区的时间字符串" not in page
    assert "sample.md" in page
    assert task_payload["status"] == "completed"

    with get_session_factory()() as session:
        document = DocumentRepository(session).get_by_path(sample_path)
        ingest_state = IngestStateRepository(session).get_by_source_and_scope("fs", scope_key)

    assert document is not None
    assert document.file_modified_time is not None
    assert document.file_modified_time.tzinfo == UTC
    assert ingest_state is not None
    assert ingest_state.cursor_updated_at is not None
    assert ingest_state.cursor_updated_at.tzinfo == UTC


def test_index_task_status_route_returns_running_task_snapshot(
    test_environment: Path,
    copied_documents_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证索引状态接口会返回后台任务快照。"""

    del test_environment
    container = build_service_container(force_reload=True)
    original_index_documents = container.index_service.index_documents

    def slow_index_documents(
        request: IndexRequest,
        on_progress: Callable[[IndexProgressEvent], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> IndexResult:
        sleep(0.15)
        return original_index_documents(
            request,
            on_progress=on_progress,
            should_cancel=should_cancel,
        )

    monkeypatch.setattr(container.index_service, "index_documents", slow_index_documents)

    app = create_app(services=container)
    app.config["TESTING"] = True

    with app.test_client() as client:
        response = client.post(
            "/index",
            data={
                "path": str(copied_documents_dir),
                "recursive": "on",
                "force": "",
                "file_types": ["md"],
            },
            follow_redirects=False,
        )
        assert response.status_code == 302
        redirect_location = response.headers["Location"]
        parsed_location = urlparse(redirect_location)
        task_id = parse_qs(parsed_location.query)["task_id"][0]

        task_response = client.get(f"/index/tasks/{task_id}")
        assert task_response.status_code == 200
        task_payload = task_response.get_json()

        assert isinstance(task_payload, dict)
        assert task_payload["task_id"] == task_id
        assert task_payload["status"] in {"queued", "running", "completed"}

        completed_payload = _wait_for_index_task_completion(client, task_id)
        assert completed_payload["status"] == "completed"


def test_index_page_keeps_showing_latest_task_after_navigation(
    test_environment: Path,
    copied_documents_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证切到其他管理页再回到索引页时，仍能看到最近的索引任务。"""

    del test_environment
    container = build_service_container(force_reload=True)
    original_index_documents = container.index_service.index_documents

    def slow_index_documents(
        request: IndexRequest,
        on_progress: Callable[[IndexProgressEvent], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> IndexResult:
        sleep(0.2)
        return original_index_documents(
            request,
            on_progress=on_progress,
            should_cancel=should_cancel,
        )

    monkeypatch.setattr(container.index_service, "index_documents", slow_index_documents)

    app = create_app(services=container)
    app.config["TESTING"] = True

    with app.test_client() as client:
        response = client.post(
            "/index",
            data={
                "path": str(copied_documents_dir),
                "recursive": "on",
                "force": "",
                "file_types": ["md"],
            },
            follow_redirects=False,
        )
        assert response.status_code == 302
        redirect_location = response.headers["Location"]
        parsed_location = urlparse(redirect_location)
        task_id = parse_qs(parsed_location.query)["task_id"][0]

        errors_page = client.get("/errors")
        assert errors_page.status_code == 200

        index_page = client.get("/index")
        assert index_page.status_code == 200
        page = index_page.get_data(as_text=True)
        assert task_id in page
        assert "索引任务" in page

        completed_payload = _wait_for_index_task_completion(client, task_id)
        assert completed_payload["status"] == "completed"


def test_cancel_index_task_route_marks_task_cancelled(
    test_environment: Path,
    copied_documents_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证可以取消后台索引任务，并看到取消后的最终状态。"""

    del test_environment
    container = build_service_container(force_reload=True)
    original_index_documents = container.index_service.index_documents

    def slow_index_documents(
        request: IndexRequest,
        on_progress: Callable[[IndexProgressEvent], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> IndexResult:
        sleep(0.2)
        if should_cancel is not None and should_cancel():
            raise IndexCancelledError("索引任务已取消。")
        return original_index_documents(
            request,
            on_progress=on_progress,
            should_cancel=should_cancel,
        )

    monkeypatch.setattr(container.index_service, "index_documents", slow_index_documents)

    app = create_app(services=container)
    app.config["TESTING"] = True

    with app.test_client() as client:
        response = client.post(
            "/index",
            data={
                "path": str(copied_documents_dir),
                "recursive": "on",
                "force": "",
                "file_types": ["md"],
            },
            follow_redirects=False,
        )
        assert response.status_code == 302
        redirect_location = response.headers["Location"]
        parsed_location = urlparse(redirect_location)
        task_id = parse_qs(parsed_location.query)["task_id"][0]

        cancel_response = client.post(f"/index/tasks/{task_id}/cancel")
        assert cancel_response.status_code == 200
        cancel_payload = cancel_response.get_json()
        assert isinstance(cancel_payload, dict)
        assert cancel_payload["status"] in {"cancelling", "cancelled"}

        final_payload = _wait_for_index_task_completion(client, task_id)
        assert final_payload["status"] == "cancelled"
        assert final_payload["error_message"] == "索引任务已取消。"

        index_page = client.get("/index")
        page = index_page.get_data(as_text=True)
        assert task_id in page
        assert "已取消" in page


@pytest.mark.parametrize(
    ("route", "active_text"),
    [
        ("/index", "文档索引"),
        ("/errors", "错误记录"),
        ("/clean", "清理索引"),
    ],
)
def test_management_pages_use_tabs_instead_of_fake_search(
    test_environment: Path,
    route: str,
    active_text: str,
) -> None:
    """验证管理页使用统一页签导航，而不是伪装成搜索框的导航入口。"""
    del test_environment
    app = create_app()
    app.config["TESTING"] = True

    with app.test_client() as client:
        response = client.get(route)

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "返回文档检索" not in page
    assert "返回搜索" in page
    assert "文档索引" in page
    assert "错误记录" in page
    assert "清理索引" in page
    assert active_text in page


def test_index_page_groups_file_types_and_supports_batch_actions(test_environment: Path) -> None:
    """验证索引页会按类别展示文件类型，并提供批量选择操作。"""

    del test_environment
    app = create_app()
    app.config["TESTING"] = True

    with app.test_client() as client:
        response = client.get("/index")

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Office / PDF" in page
    assert "Markdown / 文本" in page
    assert "网页" in page
    assert "思维导图" in page
    assert "流程图" in page
    assert "图片" in page
    assert "视频" in page
    assert "draw.io (.drawio)" in page
    assert "FLV (.flv)" in page
    assert "Markdown (.markdown)" not in page
    assert "全选" in page
    assert "全不选" in page
    assert "反选" in page
    assert "已选" not in page


def test_index_page_requires_at_least_one_file_type(test_environment: Path) -> None:
    """验证索引页提交时至少要选择一种文件类型。"""

    del test_environment
    app = create_app()
    app.config["TESTING"] = True

    with app.test_client() as client:
        response = client.post(
            "/index",
            data={
                "path": "",
                "recursive": "on",
                "force": "",
            },
            follow_redirects=False,
        )

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "请至少选择一种文件类型。" in page


def test_clean_page_initially_shows_compare_step_only(test_environment: Path) -> None:
    """验证清理页默认只展示“开始对比”的第一步。"""

    del test_environment
    app = create_app()
    app.config["TESTING"] = True

    with app.test_client() as client:
        response = client.get("/clean")

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "第一步：输入目录并开始对比" in page
    assert "开始对比" in page
    assert "第二步：筛选对比结果并选择要删除的记录" not in page
    assert "文件类型过滤" not in page
    assert "删除选中" not in page


def test_clean_page_compare_shows_filters_and_results(
    test_environment: Path,
    tmp_path: Path,
) -> None:
    """验证开始对比后会出现结果筛选区和可勾选的对比结果。"""

    del test_environment
    initialize_database()
    container = build_service_container(force_reload=True)
    clean_docs_dir = tmp_path / "clean-documents"
    clean_docs_dir.mkdir()
    orphan_file = clean_docs_dir / "orphan.md"
    orphan_file.write_text("# orphan\n", encoding="utf-8")
    existing_file = clean_docs_dir / "keep.md"
    existing_file.write_text("# keep\n", encoding="utf-8")

    container.index_service.index_documents(
        IndexRequest(
            paths=(clean_docs_dir,),
            recursive=True,
            modified_since=None,
            file_types=("md",),
            force=False,
        )
    )
    orphan_file.unlink()

    app = create_app(services=container)
    app.config["TESTING"] = True

    with app.test_client() as client:
        response = client.post(
            "/clean",
            data={
                "action": "compare",
                "path": str(clean_docs_dir),
            },
        )

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "第二步：筛选对比结果并选择要删除的记录" in page
    assert "路径关键词" in page
    assert "文件类型过滤" in page
    assert "Markdown / 文本" in page
    assert "流程图" in page
    assert "draw.io (.drawio)" in page
    assert "FLV (.flv)" in page
    assert "Markdown (.markdown)" not in page
    assert "删除选中" in page
    assert "删除全部失效记录" in page
    assert "selected_document_ids" in page
    assert "orphan.md" in page
    assert "keep.md" not in page


def test_clean_page_delete_selected_removes_only_checked_items(
    test_environment: Path,
    tmp_path: Path,
) -> None:
    """验证清理页只会删除当前勾选的孤儿记录。"""

    del test_environment
    initialize_database()
    container = build_service_container(force_reload=True)
    clean_docs_dir = tmp_path / "clean-targets"
    clean_docs_dir.mkdir()
    first_orphan = clean_docs_dir / "first.md"
    first_orphan.write_text("# first\n", encoding="utf-8")
    second_orphan = clean_docs_dir / "second.md"
    second_orphan.write_text("# second\n", encoding="utf-8")

    container.index_service.index_documents(
        IndexRequest(
            paths=(clean_docs_dir,),
            recursive=True,
            modified_since=None,
            file_types=("md",),
            force=False,
        )
    )

    with get_session_factory()() as session:
        repository = DocumentRepository(session)
        first_document = repository.get_by_path(str(first_orphan.resolve()))
        second_document = repository.get_by_path(str(second_orphan.resolve()))

    assert first_document is not None
    assert second_document is not None

    first_orphan.unlink()
    second_orphan.unlink()

    app = create_app(services=container)
    app.config["TESTING"] = True

    with app.test_client() as client:
        response = client.post(
            "/clean",
            data={
                "action": "delete",
                "path": str(clean_docs_dir),
                "selected_document_ids": [str(first_document.id)],
            },
        )

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "已删除 1 条记录。" in page
    assert "first.md" not in page
    assert "second.md" in page

    with get_session_factory()() as session:
        repository = DocumentRepository(session)
        remaining_documents = repository.list_documents_under_scope(str(clean_docs_dir.resolve()))

    assert [document.file_name for document in remaining_documents] == ["second.md"]


def test_clean_page_delete_all_removes_all_matched_items(
    test_environment: Path,
    tmp_path: Path,
) -> None:
    """验证清理页可一次删除当前对比结果中的全部失效记录。"""

    del test_environment
    initialize_database()
    container = build_service_container(force_reload=True)
    clean_docs_dir = tmp_path / "clean-all-targets"
    clean_docs_dir.mkdir()
    first_orphan = clean_docs_dir / "first.md"
    first_orphan.write_text("# first\n", encoding="utf-8")
    second_orphan = clean_docs_dir / "second.md"
    second_orphan.write_text("# second\n", encoding="utf-8")

    container.index_service.index_documents(
        IndexRequest(
            paths=(clean_docs_dir,),
            recursive=True,
            modified_since=None,
            file_types=("md",),
            force=False,
        )
    )

    first_orphan.unlink()
    second_orphan.unlink()

    app = create_app(services=container)
    app.config["TESTING"] = True

    with app.test_client() as client:
        response = client.post(
            "/clean",
            data={
                "action": "delete_all",
                "path": str(clean_docs_dir),
            },
        )

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "已删除 2 条记录。" in page
    assert "first.md" not in page
    assert "second.md" not in page
    assert "当前没有命中的对比结果。" in page

    with get_session_factory()() as session:
        repository = DocumentRepository(session)
        remaining_documents = repository.list_documents_under_scope(str(clean_docs_dir.resolve()))

    assert remaining_documents == []


def test_errors_page_places_retry_all_action_in_result_header(
    test_environment: Path,
    copied_documents_dir: Path,
) -> None:
    """验证“重试全部失败记录”位于结果区右上角，而不是顶部筛选区。"""

    del test_environment
    initialize_database()
    container = build_service_container(force_reload=True)
    container.index_service.index_documents(
        IndexRequest(
            paths=(copied_documents_dir,),
            recursive=True,
            modified_since=None,
            file_types=tuple(),
            force=False,
        )
    )

    app = create_app(services=container)
    app.config["TESTING"] = True

    with app.test_client() as client:
        response = client.get("/errors")

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert page.count("重试全部失败记录") == 1
    assert "共 1 条失败记录" in page
    assert page.index("共 1 条失败记录") < page.index("重试全部失败记录")


def test_errors_page_supports_checkbox_batch_retry(
    test_environment: Path,
    copied_documents_dir: Path,
) -> None:
    """验证错误记录页支持勾选结果并批量重试选中项。"""

    del test_environment
    initialize_database()
    container = build_service_container(force_reload=True)
    container.index_service.index_documents(
        IndexRequest(
            paths=(copied_documents_dir,),
            recursive=True,
            modified_since=None,
            file_types=tuple(),
            force=False,
        )
    )

    app = create_app(services=container)
    app.config["TESTING"] = True

    with app.test_client() as client:
        response = client.get("/errors")

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "重试选中" in page
    assert "全选当前筛选结果" in page
    assert "selected_document_ids" in page


def test_errors_page_retry_selected_only_retries_checked_items(
    test_environment: Path,
    tmp_path: Path,
) -> None:
    """验证错误记录页批量重试时只处理勾选的失败记录。"""

    del test_environment
    initialize_database()
    container = build_service_container(force_reload=True)
    error_docs_dir = tmp_path / "error-docs"
    error_docs_dir.mkdir()
    first_file = error_docs_dir / "first.drawio"
    second_file = error_docs_dir / "second.drawio"
    first_file.write_text("<mxfile><diagram>broken", encoding="utf-8")
    second_file.write_text("<mxfile><diagram>broken", encoding="utf-8")

    container.index_service.index_documents(
        IndexRequest(
            paths=(error_docs_dir,),
            recursive=True,
            modified_since=None,
            file_types=("drawio",),
            force=False,
        )
    )

    repaired_content = (
        '<mxfile><diagram name="Recovered"><mxGraphModel><root>'
        '<mxCell id="0" value="sample repaired"/></root>'
        "</mxGraphModel></diagram></mxfile>"
    )
    first_file.write_text(repaired_content, encoding="utf-8")

    with get_session_factory()() as session:
        repository = DocumentRepository(session)
        first_document = repository.get_by_path(str(first_file.resolve()))
        second_document = repository.get_by_path(str(second_file.resolve()))

    assert first_document is not None
    assert second_document is not None

    app = create_app(services=container)
    app.config["TESTING"] = True

    with app.test_client() as client:
        response = client.post(
            "/errors",
            data={
                "action": "retry_selected",
                "selected_document_ids": [str(first_document.id)],
            },
        )

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "本次重试：共 1 条，成功 1，失败 0。" in page
    assert "first.drawio" not in page
    assert "second.drawio" in page

    with get_session_factory()() as session:
        repository = DocumentRepository(session)
        refreshed_first = repository.get_by_id(first_document.id)
        refreshed_second = repository.get_by_id(second_document.id)

    assert refreshed_first is not None
    assert refreshed_second is not None
    assert refreshed_first.status == "completed"
    assert refreshed_second.status == "failed"


def test_errors_page_can_include_and_retry_fallback_records(
    test_environment: Path,
    tmp_path: Path,
) -> None:
    """验证错误记录页可按筛选展示回退记录，并支持勾选后重试。"""

    del test_environment
    initialize_database()
    container = build_service_container(force_reload=True)
    fallback_file = tmp_path / "fallback.pdf"
    fallback_file.write_bytes(b"%PDF-1.4 fallback")
    fallback_document_id = _insert_document_record(
        file_path=fallback_file,
        status="fallback",
        content_markdown="# fallback document",
        error_message="回退为元数据索引",
        conversion_type=ConversionType.STRUCTURED_TO_MD,
    )

    class FakeSuccessfulConverter:
        """模拟重试后成功拿到全文的转换器。"""

        def convert(self, source_path: Path) -> ConversionResult:
            assert source_path == fallback_file
            return ConversionResult(
                content_markdown="# repaired fallback",
                conversion_type=ConversionType.STRUCTURED_TO_MD,
                status=ConversionStatus.COMPLETED,
                error_message=None,
            )

    class FakeConverterFactory(ConverterFactory):
        """为测试注入固定的成功转换器。"""

        def __init__(self) -> None:
            pass

        def create_converter(self, source_path: Path) -> FakeSuccessfulConverter:
            assert source_path == fallback_file
            return FakeSuccessfulConverter()

    container.error_record_service._converter_factory = FakeConverterFactory()
    app = create_app(services=container)
    app.config["TESTING"] = True

    with app.test_client() as client:
        default_response = client.get("/errors")
        include_response = client.get("/errors?include_fallback=1")
        retry_response = client.post(
            "/errors",
            data={
                "action": "retry_selected",
                "include_fallback": "1",
                "selected_document_ids": [str(fallback_document_id)],
            },
        )

    assert default_response.status_code == 200
    assert fallback_file.name not in default_response.get_data(as_text=True)

    assert include_response.status_code == 200
    include_page = include_response.get_data(as_text=True)
    assert fallback_file.name in include_page
    assert "共 1 条问题记录" in include_page
    assert "原因说明" in include_page

    assert retry_response.status_code == 200
    retry_page = retry_response.get_data(as_text=True)
    assert "本次重试：共 1 条，成功 1，失败 0。" in retry_page
    assert fallback_file.name not in retry_page

    with get_session_factory()() as session:
        refreshed_document = DocumentRepository(session).get_by_id(fallback_document_id)

    assert refreshed_document is not None
    assert refreshed_document.status == "completed"
    assert refreshed_document.error_message is None


def test_search_homepage_renders_search_shell(test_environment: Path) -> None:
    """验证首页默认展示搜索入口态。"""
    del test_environment
    app = create_app()
    app.config["TESTING"] = True

    with app.test_client() as client:
        response = client.get("/search")

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "输入本地文档关键词" in page
    assert "本地文档搜索助手" in page
    assert "最近搜索" in page
    assert "搜索" in page
    assert "清除全部" in page
    assert "RECENT_HISTORY_LIMIT = 20" in page
    assert "仅检索已建立索引的本机文档" not in page
    assert "Local-first document search" not in page


def test_search_results_page_renders_google_like_result_shell(
    test_environment: Path,
    copied_documents_dir: Path,
) -> None:
    """验证搜索结果页使用新的结果布局并展示命中文档。"""
    del test_environment
    initialize_database()
    container = build_service_container(force_reload=True)
    container.index_service.index_documents(
        IndexRequest(
            paths=(copied_documents_dir,),
            recursive=True,
            modified_since=None,
            file_types=tuple(),
            force=False,
        )
    )

    app = create_app(services=container)
    app.config["TESTING"] = True

    with app.test_client() as client:
        response = client.get("/search?q=sample")

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "sample.md" in page
    assert "打开所在目录" in page
    assert "修改时间" in page
    assert "搜索" in page
    assert "data-system-open" in page
    assert 'target="_blank"' in page
    assert 'rel="noopener noreferrer"' in page
    assert "本地索引" not in page
    assert "文档预览" not in page
    assert "打开应用菜单" not in page


def test_preview_page_renders_markdown_preview_and_source_tabs(
    test_environment: Path,
    copied_documents_dir: Path,
) -> None:
    """验证预览页默认展示 Markdown 渲染结果，并提供原文切换。"""

    del test_environment
    initialize_database()
    container = build_service_container(force_reload=True)
    index_result = container.index_service.index_documents(
        IndexRequest(
            paths=(copied_documents_dir,),
            recursive=True,
            modified_since=None,
            file_types=tuple(),
            force=False,
        )
    )
    document_id = next(
        item.document_id for item in index_result.files if item.file_path.endswith("sample.md")
    )

    app = create_app(services=container)
    app.config["TESTING"] = True

    with app.test_client() as client:
        response = client.get(f"/documents/{document_id}/preview")

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "渲染预览" in page
    assert "Markdown 原文" in page
    assert "Preview" not in page
    assert "completed" not in page
    assert page.index("返回搜索") < page.index("打开文件") < page.index("打开所在目录")
    assert "btn btn-primary" not in page
    assert 'data-preview-tab-panel="rendered"' in page
    assert 'data-preview-tab-panel="markdown"' in page
    assert "<h1" in page


def test_document_service_uses_markdown_it_for_tables_and_escapes_raw_html(
    test_environment: Path,
    tmp_path: Path,
) -> None:
    """验证预览服务会渲染表格，并转义 Markdown 里的原生 HTML。"""

    del test_environment
    initialize_database()
    container = build_service_container(force_reload=True)

    docs_dir = tmp_path / "markdown-preview"
    docs_dir.mkdir()
    markdown_file = docs_dir / "table.md"
    markdown_file.write_text(
        "\n".join(
            [
                "# 表格预览",
                "",
                "| 名称 | 值 |",
                "| --- | --- |",
                "| 历史 | 文档 |",
                "",
                "<script>alert('xss')</script>",
            ]
        ),
        encoding="utf-8",
    )

    index_result = container.index_service.index_documents(
        IndexRequest(
            paths=(docs_dir,),
            recursive=True,
            modified_since=None,
            file_types=tuple(),
            force=False,
        )
    )
    document_id = next(
        item.document_id for item in index_result.files if item.document_id is not None
    )

    preview_result = container.document_service.preview_document(
        DocumentPreviewRequest(document_id=document_id)
    )

    assert "<table>" in preview_result.content_html
    assert "<th>名称</th>" in preview_result.content_html
    assert "&lt;script&gt;alert('xss')&lt;/script&gt;" in preview_result.content_html
    assert "<script>" not in preview_result.content_html


def test_open_document_redirects_back_to_referrer(
    test_environment: Path,
    copied_documents_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证从搜索结果打开文件后会回到来源页，而不是跳到预览页。"""
    del test_environment
    initialize_database()
    container = build_service_container(force_reload=True)
    index_result = container.index_service.index_documents(
        IndexRequest(
            paths=(copied_documents_dir,),
            recursive=True,
            modified_since=None,
            file_types=tuple(),
            force=False,
        )
    )
    document_id = next(
        item.document_id for item in index_result.files if item.file_path.endswith("sample.md")
    )

    opened_requests: list[tuple[int, bool]] = []

    def fake_open_file(request_data: FileOpenRequest) -> None:
        opened_requests.append((request_data.document_id, request_data.open_parent_directory))

    monkeypatch.setattr(container.file_opener_service, "open_file", fake_open_file)

    app = create_app(services=container)
    app.config["TESTING"] = True

    with app.test_client() as client:
        response = client.get(
            f"/documents/{document_id}/open",
            headers={"Referer": "/search?q=sample"},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["Location"] == "/search?q=sample"
    assert opened_requests == [(document_id, False)]


def test_open_document_post_returns_no_content_for_async_trigger(
    test_environment: Path,
    copied_documents_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证异步打开文件时不会触发页面跳转。"""
    del test_environment
    initialize_database()
    container = build_service_container(force_reload=True)
    index_result = container.index_service.index_documents(
        IndexRequest(
            paths=(copied_documents_dir,),
            recursive=True,
            modified_since=None,
            file_types=tuple(),
            force=False,
        )
    )
    document_id = next(
        item.document_id for item in index_result.files if item.file_path.endswith("sample.md")
    )

    opened_requests: list[tuple[int, bool]] = []

    def fake_open_file(request_data: FileOpenRequest) -> None:
        opened_requests.append((request_data.document_id, request_data.open_parent_directory))

    monkeypatch.setattr(container.file_opener_service, "open_file", fake_open_file)

    app = create_app(services=container)
    app.config["TESTING"] = True

    with app.test_client() as client:
        response = client.post(
            f"/documents/{document_id}/open",
            follow_redirects=False,
        )

    assert response.status_code == 204
    assert response.get_data(as_text=True) == ""
    assert opened_requests == [(document_id, False)]


def test_open_parent_directory_uses_parent_directory_flag(
    test_environment: Path,
    copied_documents_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证打开所在目录会触发父目录打开逻辑。"""
    del test_environment
    initialize_database()
    container = build_service_container(force_reload=True)
    index_result = container.index_service.index_documents(
        IndexRequest(
            paths=(copied_documents_dir,),
            recursive=True,
            modified_since=None,
            file_types=tuple(),
            force=False,
        )
    )
    document_id = next(
        item.document_id for item in index_result.files if item.file_path.endswith("sample.md")
    )

    opened_requests: list[tuple[int, bool]] = []

    def fake_open_file(request_data: FileOpenRequest) -> None:
        opened_requests.append((request_data.document_id, request_data.open_parent_directory))

    monkeypatch.setattr(container.file_opener_service, "open_file", fake_open_file)

    app = create_app(services=container)
    app.config["TESTING"] = True

    with app.test_client() as client:
        response = client.post(
            f"/documents/{document_id}/open?parent=1",
            follow_redirects=False,
        )

    assert response.status_code == 204
    assert opened_requests == [(document_id, True)]


def test_clean_service_respects_directory_boundaries(
    test_environment: Path,
    tmp_path: Path,
) -> None:
    """验证清理服务不会把相似前缀目录误判为同一作用域。"""
    del test_environment
    initialize_database()
    container = build_service_container(force_reload=True)

    docs_dir = tmp_path / "docs"
    docs2_dir = tmp_path / "docs2"
    docs_dir.mkdir()
    docs2_dir.mkdir()
    (docs_dir / "inside.md").write_text("# inside", encoding="utf-8")
    (docs2_dir / "outside.md").write_text("# outside", encoding="utf-8")

    container.index_service.index_documents(
        IndexRequest(
            paths=(docs_dir, docs2_dir),
            recursive=True,
            modified_since=None,
            file_types=tuple(),
            force=False,
        )
    )

    (docs_dir / "inside.md").unlink()
    (docs2_dir / "outside.md").unlink()

    clean_result = container.clean_service.clean_orphans(
        CleanRequest(scope_path=docs_dir, dry_run=False)
    )
    assert clean_result.matched == 1
    assert clean_result.deleted == 1
    assert len(clean_result.items) == 1
    assert Path(clean_result.items[0].file_path).is_relative_to(docs_dir.resolve())

    remaining_result = container.clean_service.clean_orphans(
        CleanRequest(scope_path=docs2_dir, dry_run=True)
    )
    assert remaining_result.matched == 1
    assert len(remaining_result.items) == 1
    assert Path(remaining_result.items[0].file_path).is_relative_to(docs2_dir.resolve())


def test_bootstrap_runtime_regular_startup_skips_full_search_rebuild(
    test_environment: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证常规启动不会重复触发全文索引重建。"""
    del test_environment
    rebuild_calls: list[str] = []

    def fake_rebuild_search_index() -> None:
        rebuild_calls.append("called")

    monkeypatch.setattr(database_module, "rebuild_search_index", fake_rebuild_search_index)
    bootstrap_runtime(force_reload=True)

    assert rebuild_calls == []


def test_create_app_uses_explicit_config_for_service_container(test_environment: Path) -> None:
    """验证显式传入的配置会继续传递给 Service 容器。"""
    del test_environment
    base_config = load_app_config(force_reload=True)
    custom_config = replace(base_config, flask_port=5999)

    app = create_app(config=custom_config)

    services = app.extensions["services"]
    assert isinstance(services, ServiceContainer)
    assert app.config["FLASK_PORT"] == 5999
    assert services.config.flask_port == 5999

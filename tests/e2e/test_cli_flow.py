"""CLI 端到端流程测试。"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

import local_document_search.cli as cli_module
from local_document_search.cli import app
from local_document_search.converters.base import ConversionType
from local_document_search.persistence.database import get_session_factory
from local_document_search.persistence.repositories import DocumentRepository, DocumentUpsertInput
from local_document_search.services import DatabaseMigrationRequest, DatabaseMigrationResult

runner = CliRunner()


def test_cli_full_flow(test_environment: Path, copied_documents_dir: Path) -> None:
    """覆盖初始化、索引、搜索、重试与清理主流程。"""
    del test_environment

    db_result = runner.invoke(app, ["db", "init", "--format", "json"])
    assert db_result.exit_code == 0
    assert json.loads(db_result.stdout)["status"] == "initialized"

    index_result = runner.invoke(app, ["index", str(copied_documents_dir), "--format", "json"])
    assert index_result.exit_code == 0
    index_payload = json.loads(index_result.stdout)
    assert index_payload["processed"] == 2
    assert index_payload["errors"] == 1

    search_result = runner.invoke(app, ["search", "sample", "--format", "json"])
    assert search_result.exit_code == 0
    search_payload = json.loads(search_result.stdout)
    assert search_payload["total"] >= 2

    retry_result = runner.invoke(
        app,
        [
            "errors",
            "retry",
            "--path",
            str((copied_documents_dir / "broken.drawio").resolve()),
            "--format",
            "json",
        ],
    )
    assert retry_result.exit_code == 0
    retry_payload = json.loads(retry_result.stdout)
    assert retry_payload["total"] == 1
    assert retry_payload["failed"] == 1

    # 删除原文件，制造孤儿记录以验证 clean 流程。
    (copied_documents_dir / "sample.md").unlink()
    clean_result = runner.invoke(app, ["clean", str(copied_documents_dir), "--format", "json"])
    assert clean_result.exit_code == 0
    clean_payload = json.loads(clean_result.stdout)
    assert clean_payload["matched"] >= 1
    assert clean_payload["deleted"] >= 1


def test_cli_dry_run_flow(test_environment: Path, copied_documents_dir: Path) -> None:
    """覆盖 index 与 retry 的 dry-run 行为。"""

    del test_environment

    db_result = runner.invoke(app, ["db", "init", "--format", "json"])
    assert db_result.exit_code == 0

    index_result = runner.invoke(
        app,
        ["index", str(copied_documents_dir), "--dry-run", "--format", "json"],
    )
    assert index_result.exit_code == 0
    index_payload = json.loads(index_result.stdout)
    assert index_payload["dry_run"] is True
    assert index_payload["planned"] == 3
    assert index_payload["processed"] == 0

    search_result = runner.invoke(app, ["search", "sample", "--format", "json"])
    assert search_result.exit_code == 0
    assert json.loads(search_result.stdout)["total"] == 0

    actual_index_result = runner.invoke(
        app, ["index", str(copied_documents_dir), "--format", "json"]
    )
    assert actual_index_result.exit_code == 0

    retry_result = runner.invoke(
        app,
        [
            "errors",
            "retry",
            "--path",
            str((copied_documents_dir / "broken.drawio").resolve()),
            "--dry-run",
            "--format",
            "json",
        ],
    )
    assert retry_result.exit_code == 0
    retry_payload = json.loads(retry_result.stdout)
    assert retry_payload["dry_run"] is True
    assert retry_payload["planned"] == 1
    assert retry_payload["succeeded"] == 0
    assert retry_payload["failed"] == 0


def test_cli_errors_list_can_include_fallback_records(
    test_environment: Path,
    tmp_path: Path,
) -> None:
    """验证 CLI 可通过 --include-fallback 列出回退记录。"""

    del test_environment
    db_result = runner.invoke(app, ["db", "init", "--format", "json"])
    assert db_result.exit_code == 0

    fallback_file = tmp_path / "fallback.pdf"
    fallback_file.write_bytes(b"%PDF-1.4 fallback")
    with get_session_factory()() as session:
        repository = DocumentRepository(session)
        repository.upsert(
            DocumentUpsertInput(
                file_name=fallback_file.name,
                file_type="pdf",
                file_size=fallback_file.stat().st_size,
                file_created_at=datetime.fromtimestamp(fallback_file.stat().st_ctime, tz=UTC),
                file_modified_time=datetime.fromtimestamp(fallback_file.stat().st_mtime, tz=UTC),
                file_path=str(fallback_file.resolve()),
                content_markdown="# fallback document",
                conversion_type=int(ConversionType.STRUCTURED_TO_MD),
                status="fallback",
                error_message="回退为元数据索引",
                source="fs",
                source_url=None,
            )
        )
        session.commit()

    default_result = runner.invoke(app, ["errors", "list", "--format", "json"])
    assert default_result.exit_code == 0
    assert json.loads(default_result.stdout)["total"] == 0

    include_result = runner.invoke(
        app,
        ["errors", "list", "--include-fallback", "--format", "json"],
    )
    assert include_result.exit_code == 0
    include_payload = json.loads(include_result.stdout)
    assert include_payload["total"] == 1
    assert include_payload["include_fallback"] is True
    assert include_payload["items"][0]["status"] == "fallback"


def test_cli_db_migrate_to_postgres_uses_config_defaults(
    test_environment: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证迁移命令默认读取配置中的 SQLite 路径和 PostgreSQL 连接串。"""

    captured_requests: list[DatabaseMigrationRequest] = []
    del test_environment
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://tester:secret@localhost:5432/local_document_search",
    )

    def fake_migrate(
        self: object,
        request: DatabaseMigrationRequest,
    ) -> DatabaseMigrationResult:
        captured_requests.append(request)
        return DatabaseMigrationResult(
            source_sqlite_path=Path("D:/tmp/source.db"),
            target_database_target="postgresql://tester:***@localhost:5432/local_document_search",
            source_documents=2,
            source_ingest_states=1,
            migrated_documents=2,
            migrated_ingest_states=1,
            target_documents=2,
            target_ingest_states=1,
            include_ingest_state=True,
            truncated_target=False,
        )

    monkeypatch.setattr(
        cli_module.DatabaseMigrationService,
        "migrate_sqlite_to_postgresql",
        fake_migrate,
    )

    result = runner.invoke(app, ["db", "migrate-to-postgres", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "migrated"
    assert payload["target_database_target"] == (
        "postgresql://tester:***@localhost:5432/local_document_search"
    )
    assert len(captured_requests) == 1
    assert captured_requests[0].target_database_url == (
        "postgresql://tester:secret@localhost:5432/local_document_search"
    )
    assert captured_requests[0].include_ingest_state is True


def test_doc_cli_help_plain_outputs_machine_friendly_text(test_environment: Path) -> None:
    """验证 `--help-plain` 输出纯文本帮助，而不是 Rich 方框。"""

    del test_environment
    project_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "doc-cli.py", "--help-plain"],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "Usage: doc-cli.py [OPTIONS] COMMAND [ARGS]..." in result.stdout
    assert "Commands:" in result.stdout
    assert "index" in result.stdout
    assert "errors" in result.stdout
    assert "+- Options" not in result.stdout
    assert "输出适合 AI Agent 和脚本读取的纯文本帮助" in result.stdout


def test_doc_cli_help_lists_help_plain_option(test_environment: Path) -> None:
    """验证默认帮助输出会列出 `--help-plain` 选项。"""

    del test_environment
    project_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "doc-cli.py", "--help"],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "--help-plain" in result.stdout
    assert "输出适合 AI Agent 和脚本读取的纯文本帮助。" in result.stdout


def test_doc_cli_cmd_wrapper_works_outside_project_dir(
    test_environment: Path, tmp_path: Path
) -> None:
    """验证 `.cmd` 包装脚本在项目目录外也能正确加载项目依赖。"""

    del test_environment
    if sys.platform != "win32":
        return

    project_root = Path(__file__).resolve().parents[2]
    wrapper_path = project_root / "scripts" / "doc-cli.cmd"
    result = subprocess.run(
        ["cmd", "/c", str(wrapper_path), "--help-plain"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "本地文档搜索助手 CLI" in result.stdout
    assert "Commands:" in result.stdout

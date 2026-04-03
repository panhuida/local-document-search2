"""CLI 端到端流程测试。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

from local_document_search.cli import app

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

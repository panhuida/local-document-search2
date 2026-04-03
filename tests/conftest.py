"""pytest 全局夹具。"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


@pytest.fixture
def test_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """为每个测试准备隔离的 SQLite 数据库环境。"""
    database_path = tmp_path / "search.db"
    monkeypatch.setenv("DATABASE_BACKEND", "sqlite")
    monkeypatch.setenv("SQLITE_DB_PATH", str(database_path))
    monkeypatch.setenv("SEARCH_DIRS", "")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    from local_document_search import bootstrap_runtime

    bootstrap_runtime(force_reload=True)
    return database_path


@pytest.fixture
def copied_documents_dir(tmp_path: Path) -> Path:
    """复制一份测试文档样本，避免直接修改 fixtures 原件。"""
    source_dir = PROJECT_ROOT / "tests" / "fixtures" / "documents"
    target_dir = tmp_path / "documents"
    shutil.copytree(source_dir, target_dir)
    return target_dir

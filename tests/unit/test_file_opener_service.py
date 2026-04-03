"""文件打开服务单元测试。"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from sqlalchemy.orm import Session, sessionmaker

from local_document_search.services import FileOpenerService
from local_document_search.services import file_opener_service as file_opener_service_module


def test_open_parent_directory_uses_windows_explorer_select(
    monkeypatch,
) -> None:
    """验证 Windows 下打开所在目录会使用资源管理器定位文件。"""
    service = FileOpenerService(cast(sessionmaker[Session], None))
    opened_commands: list[list[str]] = []
    started_paths: list[str] = []

    def fake_popen(command: list[str]) -> None:
        opened_commands.append(command)

    def fake_startfile(path: str) -> None:
        started_paths.append(path)

    monkeypatch.setattr(file_opener_service_module.sys, "platform", "win32", raising=False)
    monkeypatch.setattr(file_opener_service_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(file_opener_service_module.os, "startfile", fake_startfile, raising=False)

    service._open_path(Path(r"C:\documents\历史\sample.md"), open_parent_directory=True)

    assert opened_commands == [["explorer.exe", "/select,", r"C:\documents\历史\sample.md"]]
    assert started_paths == []


def test_open_file_uses_startfile_on_windows(monkeypatch) -> None:
    """验证 Windows 下打开文件本身仍然走系统默认打开方式。"""
    service = FileOpenerService(cast(sessionmaker[Session], None))
    opened_commands: list[list[str]] = []
    started_paths: list[str] = []

    def fake_popen(command: list[str]) -> None:
        opened_commands.append(command)

    def fake_startfile(path: str) -> None:
        started_paths.append(path)

    monkeypatch.setattr(file_opener_service_module.sys, "platform", "win32", raising=False)
    monkeypatch.setattr(file_opener_service_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(file_opener_service_module.os, "startfile", fake_startfile, raising=False)

    service._open_path(Path(r"C:\documents\历史\sample.md"), open_parent_directory=False)

    assert started_paths == [r"C:\documents\历史\sample.md"]
    assert opened_commands == []

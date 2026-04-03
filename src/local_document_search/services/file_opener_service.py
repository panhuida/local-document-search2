"""打开原始文件的服务。"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from local_document_search.exceptions import DocumentNotFoundError, FileOpenError
from local_document_search.persistence.repositories import DocumentRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FileOpenRequest:
    """打开文件或父目录的请求。"""

    document_id: int
    open_parent_directory: bool = False


@dataclass(frozen=True)
class FileOpenResult:
    """系统打开操作的结果。"""

    success: bool
    target_path: str
    message: str


class FileOpenerService:
    """负责调用操作系统打开文件或目录。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def open_file(self, request: FileOpenRequest) -> FileOpenResult:
        """根据文档记录打开文件本身或其父目录。"""
        with self._session_factory() as session:
            repository = DocumentRepository(session)
            document = repository.get_by_id(request.document_id)
            if document is None:
                raise DocumentNotFoundError(f"文档不存在：{request.document_id}")

            file_path = Path(document.file_path)
            target = file_path.parent if request.open_parent_directory else file_path
            if not target.exists():
                raise FileOpenError(f"目标路径不存在：{target}")

            try:
                self._open_path(
                    file_path=file_path, open_parent_directory=request.open_parent_directory
                )
                return FileOpenResult(
                    success=True, target_path=str(target), message="已触发系统打开操作"
                )
            except Exception as exc:
                logger.exception("打开文件失败：%s", target)
                raise FileOpenError(str(exc)) from exc

    def _open_path(self, file_path: Path, open_parent_directory: bool) -> None:
        """按当前平台选择合适的打开命令。"""
        normalized_file_path = file_path.resolve(strict=False)
        target = normalized_file_path.parent if open_parent_directory else normalized_file_path
        if sys.platform.startswith("win"):
            if open_parent_directory:
                # Explorer 对 /select 参数的解析比较挑剔，拆成独立参数更稳。
                subprocess.Popen(["explorer.exe", "/select,", str(normalized_file_path)])
                return
            os.startfile(str(target))
            return
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
            return
        subprocess.Popen(["xdg-open", str(target)])

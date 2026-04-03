"""孤儿记录清理服务。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from local_document_search.persistence.repositories import DocumentRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OrphanRecordItem:
    """单条孤儿记录信息。"""

    document_id: int
    file_name: str
    file_path: str
    file_type: str | None
    status: str


@dataclass(frozen=True)
class CleanRequest:
    """孤儿记录扫描与清理请求。"""

    scope_path: Path | None
    dry_run: bool
    file_types: tuple[str, ...] = tuple()
    path_keyword: str | None = None


@dataclass(frozen=True)
class DeleteSelectedOrphansRequest:
    """删除选中孤儿记录的请求。"""

    scope_path: Path | None
    document_ids: tuple[int, ...]
    file_types: tuple[str, ...] = tuple()
    path_keyword: str | None = None


@dataclass(frozen=True)
class CleanResult:
    """孤儿记录清理结果。"""

    scanned: int
    matched: int
    deleted: int
    items: tuple[OrphanRecordItem, ...]


@dataclass(frozen=True)
class DeleteSelectedOrphansResult:
    """删除选中孤儿记录的结果。"""

    requested: int
    eligible: int
    deleted: int


class CleanService:
    """负责扫描并删除数据库中的孤儿文档记录。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def compare_orphans(self, request: CleanRequest) -> CleanResult:
        """按路径范围和筛选条件对比孤儿记录，但不执行删除。"""

        scanned, orphan_items = self._collect_orphan_items(request)
        return CleanResult(
            scanned=scanned,
            matched=len(orphan_items),
            deleted=0,
            items=tuple(orphan_items),
        )

    def delete_selected_orphans(
        self, request: DeleteSelectedOrphansRequest
    ) -> DeleteSelectedOrphansResult:
        """删除当前筛选范围内被选中的孤儿记录。"""

        compare_result = self.compare_orphans(
            CleanRequest(
                scope_path=request.scope_path,
                dry_run=True,
                file_types=request.file_types,
                path_keyword=request.path_keyword,
            )
        )
        eligible_ids = {item.document_id for item in compare_result.items}
        normalized_document_ids = tuple(dict.fromkeys(request.document_ids))
        deletable_ids = tuple(
            document_id for document_id in normalized_document_ids if document_id in eligible_ids
        )

        deleted = 0
        if len(deletable_ids) > 0:
            with self._session_factory() as session:
                repository = DocumentRepository(session)
                deleted = repository.delete_by_ids(deletable_ids)
                session.commit()

        return DeleteSelectedOrphansResult(
            requested=len(normalized_document_ids),
            eligible=len(deletable_ids),
            deleted=deleted,
        )

    def clean_orphans(self, request: CleanRequest) -> CleanResult:
        """按路径范围和筛选条件清理不存在的文件记录。"""
        compare_result = self.compare_orphans(request)
        if request.dry_run:
            return compare_result

        delete_result = self.delete_selected_orphans(
            DeleteSelectedOrphansRequest(
                scope_path=request.scope_path,
                document_ids=tuple(item.document_id for item in compare_result.items),
                file_types=request.file_types,
                path_keyword=request.path_keyword,
            )
        )
        return CleanResult(
            scanned=compare_result.scanned,
            matched=compare_result.matched,
            deleted=delete_result.deleted,
            items=compare_result.items,
        )

    def _collect_orphan_items(self, request: CleanRequest) -> tuple[int, list[OrphanRecordItem]]:
        """收集当前请求命中的孤儿记录。"""

        scope_root = request.scope_path.resolve() if request.scope_path is not None else None
        scope_path = str(scope_root) if scope_root is not None else None
        normalized_file_types = set(request.file_types)

        with self._session_factory() as session:
            repository = DocumentRepository(session)
            documents = repository.list_documents_under_scope(scope_path)
            scanned = len(documents)

            orphan_items: list[OrphanRecordItem] = []
            for document in documents:
                if scope_root is not None and not self._is_within_scope(
                    Path(document.file_path), scope_root
                ):
                    continue
                # 先应用用户筛选条件，再检查磁盘文件是否仍然存在。
                if (
                    normalized_file_types
                    and (document.file_type or "") not in normalized_file_types
                ):
                    continue
                if (
                    request.path_keyword
                    and request.path_keyword.lower() not in document.file_path.lower()
                ):
                    continue
                if Path(document.file_path).exists():
                    continue

                orphan_items.append(
                    OrphanRecordItem(
                        document_id=document.id,
                        file_name=document.file_name,
                        file_path=document.file_path,
                        file_type=document.file_type,
                        status=document.status,
                    )
                )

        return scanned, orphan_items

    def _is_within_scope(self, document_path: Path, scope_root: Path) -> bool:
        """判断文档路径是否真实位于目标目录内。"""
        return document_path.resolve(strict=False).is_relative_to(scope_root)

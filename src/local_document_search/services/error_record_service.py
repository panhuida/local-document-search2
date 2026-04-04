"""错误记录查询与重试服务。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from local_document_search.converters import ConverterFactory
from local_document_search.converters.base import ConversionStatus
from local_document_search.models import Document
from local_document_search.persistence.repositories import DocumentRepository, DocumentUpsertInput
from local_document_search.utils import aware_utc_from_timestamp, normalize_datetime_to_aware_utc

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ErrorListRequest:
    """错误记录列表查询请求。"""

    file_name_keyword: str | None = None
    updated_after: datetime | None = None
    updated_before: datetime | None = None
    include_fallback: bool = False


@dataclass(frozen=True)
class ErrorRecordItem:
    """单条错误记录展示模型。"""

    document_id: int
    file_name: str
    file_path: str
    file_type: str | None
    status: str
    error_message: str | None
    updated_at: datetime | None


@dataclass(frozen=True)
class ErrorListResult:
    """错误记录列表查询结果。"""

    total: int
    include_fallback: bool
    items: tuple[ErrorRecordItem, ...]


@dataclass(frozen=True)
class RetryRequest:
    """错误文档重试请求。"""

    document_id: int | None = None
    document_ids: tuple[int, ...] = tuple()
    file_path: str | None = None
    retry_all_failed: bool = False
    include_fallback: bool = False
    dry_run: bool = False


@dataclass(frozen=True)
class RetryItemResult:
    """单个重试目标的执行结果。"""

    document_id: int | None
    file_path: str
    status: str
    message: str


@dataclass(frozen=True)
class RetryResult:
    """错误重试汇总结果。"""

    total: int
    planned: int
    succeeded: int
    failed: int
    dry_run: bool
    items: tuple[RetryItemResult, ...]


class ErrorRecordService:
    """负责列出失败/回退文档并触发重新转换。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        converter_factory: ConverterFactory | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._converter_factory = converter_factory or ConverterFactory()

    def list_errors(self, request: ErrorListRequest) -> ErrorListResult:
        """查询当前数据库中的失败或回退文档列表。"""
        normalized_updated_after = (
            normalize_datetime_to_aware_utc(request.updated_after)
            if request.updated_after is not None
            else None
        )
        normalized_updated_before = (
            normalize_datetime_to_aware_utc(request.updated_before)
            if request.updated_before is not None
            else None
        )
        with self._session_factory() as session:
            repository = DocumentRepository(session)
            documents = repository.list_failed_documents(
                include_fallback=request.include_fallback,
                file_name_keyword=request.file_name_keyword,
                updated_after=normalized_updated_after,
                updated_before=normalized_updated_before,
            )
            items = tuple(
                ErrorRecordItem(
                    document_id=document.id,
                    file_name=document.file_name,
                    file_path=document.file_path,
                    file_type=document.file_type,
                    status=document.status,
                    error_message=document.error_message,
                    updated_at=document.updated_at,
                )
                for document in documents
            )
            return ErrorListResult(
                total=len(items),
                include_fallback=request.include_fallback,
                items=items,
            )

    def retry_errors(
        self,
        request: RetryRequest,
        *,
        on_targets_resolved: Callable[[int], None] | None = None,
        on_progress: Callable[[RetryItemResult], None] | None = None,
    ) -> RetryResult:
        """按请求范围重新转换失败或回退文档，并在需要时上报进度。"""
        items: list[RetryItemResult] = []
        succeeded = 0
        failed = 0

        with self._session_factory() as session:
            repository = DocumentRepository(session)
            targets = self._resolve_targets(repository, request)
            if on_targets_resolved is not None:
                on_targets_resolved(len(targets))

            if request.dry_run:
                for document in targets:
                    source_path = Path(document.file_path)
                    item = RetryItemResult(
                        document_id=document.id,
                        file_path=document.file_path,
                        status="planned",
                        message=(
                            "原文件不存在，实际执行将失败"
                            if not source_path.exists()
                            else "命中重试范围，dry-run 未执行实际重试"
                        ),
                    )
                    items.append(item)
                    if on_progress is not None:
                        on_progress(item)
                return RetryResult(
                    total=len(items),
                    planned=len(items),
                    succeeded=0,
                    failed=0,
                    dry_run=True,
                    items=tuple(items),
                )

            for document in targets:
                source_path = Path(document.file_path)
                if not source_path.exists():
                    # 原文件缺失时保留失败状态，并写回更明确的错误原因。
                    repository.upsert(
                        DocumentUpsertInput(
                            file_name=document.file_name,
                            file_type=document.file_type,
                            file_size=document.file_size,
                            file_created_at=document.file_created_at,
                            file_modified_time=document.file_modified_time,
                            file_path=document.file_path,
                            content_markdown=document.content_markdown,
                            conversion_type=document.conversion_type,
                            status="failed",
                            error_message="文件不存在，无法重试",
                            source=document.source,
                            source_url=document.source_url,
                        )
                    )
                    session.commit()
                    failed += 1
                    item = RetryItemResult(
                        document_id=document.id,
                        file_path=document.file_path,
                        status="failed",
                        message="文件不存在，无法重试",
                    )
                    items.append(item)
                    if on_progress is not None:
                        on_progress(item)
                    continue

                converter = self._converter_factory.create_converter(source_path)
                conversion = converter.convert(source_path)
                stat = source_path.stat()
                repository.upsert(
                    DocumentUpsertInput(
                        file_name=source_path.name,
                        file_type=source_path.suffix.lower().lstrip(".") or None,
                        file_size=stat.st_size,
                        file_created_at=aware_utc_from_timestamp(stat.st_ctime),
                        file_modified_time=aware_utc_from_timestamp(stat.st_mtime),
                        file_path=str(source_path.resolve()),
                        content_markdown=conversion.content_markdown,
                        conversion_type=int(conversion.conversion_type)
                        if conversion.conversion_type
                        else None,
                        status=str(conversion.status),
                        error_message=conversion.error_message,
                        source=document.source,
                        source_url=document.source_url,
                    )
                )
                session.commit()

                if conversion.status is ConversionStatus.COMPLETED:
                    succeeded += 1
                    item = RetryItemResult(
                        document_id=document.id,
                        file_path=str(source_path.resolve()),
                        status="completed",
                        message="重试成功",
                    )
                elif conversion.status is ConversionStatus.FALLBACK:
                    failed += 1
                    item = RetryItemResult(
                        document_id=document.id,
                        file_path=str(source_path.resolve()),
                        status="fallback",
                        message=conversion.error_message or "重试后仍回退为元数据索引",
                    )
                else:
                    failed += 1
                    item = RetryItemResult(
                        document_id=document.id,
                        file_path=str(source_path.resolve()),
                        status=str(conversion.status),
                        message=conversion.error_message or "重试失败",
                    )
                items.append(item)
                if on_progress is not None:
                    on_progress(item)

        return RetryResult(
            total=len(items),
            planned=0,
            succeeded=succeeded,
            failed=failed,
            dry_run=False,
            items=tuple(items),
        )

    def _resolve_targets(
        self,
        repository: DocumentRepository,
        request: RetryRequest,
    ) -> tuple[Document, ...]:
        """根据重试参数解析目标文档集合。"""
        if request.retry_all_failed:
            return tuple(
                repository.list_failed_documents(include_fallback=request.include_fallback)
            )
        if len(request.document_ids) > 0:
            normalized_document_ids = tuple(dict.fromkeys(request.document_ids))
            documents = repository.get_by_ids(normalized_document_ids)
            allowed_statuses = {"failed", "fallback"} if request.include_fallback else {"failed"}
            return tuple(document for document in documents if document.status in allowed_statuses)
        if request.document_id is not None:
            document = repository.get_by_id(request.document_id)
            allowed_statuses = {"failed", "fallback"} if request.include_fallback else {"failed"}
            return (
                (document,)
                if document is not None and document.status in allowed_statuses
                else tuple()
            )
        if request.file_path is not None:
            document = repository.get_by_path(str(Path(request.file_path).resolve()))
            allowed_statuses = {"failed", "fallback"} if request.include_fallback else {"failed"}
            return (
                (document,)
                if document is not None and document.status in allowed_statuses
                else tuple()
            )
        return tuple(repository.list_failed_documents(include_fallback=request.include_fallback))

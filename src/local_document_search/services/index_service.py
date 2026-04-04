"""文档索引服务。"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from local_document_search.converters import ConverterFactory
from local_document_search.converters.base import ConversionResult, ConversionStatus
from local_document_search.exceptions import ConfigurationError, IndexCancelledError
from local_document_search.file_types.registry import SUPPORTED_FILE_EXTENSIONS
from local_document_search.persistence.repositories import (
    DocumentRepository,
    DocumentUpsertInput,
    IngestStateRepository,
    IngestStateSummary,
)
from local_document_search.utils import (
    aware_utc_from_timestamp,
    normalize_datetime_to_aware_utc,
    utc_now,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IndexRequest:
    """文档索引请求。"""

    paths: tuple[Path, ...]
    recursive: bool
    modified_since: datetime | None
    file_types: tuple[str, ...]
    force: bool
    dry_run: bool = False
    source: str = "fs"


@dataclass(frozen=True)
class IndexProgressEvent:
    """索引过程中的单文件进度事件。"""

    file_path: str
    status: str
    message: str


@dataclass(frozen=True)
class IndexFileResult:
    """单个文件的索引结果。"""

    file_path: str
    status: str
    message: str
    document_id: int | None


@dataclass(frozen=True)
class IndexResult:
    """一次索引任务的汇总结果。"""

    scopes: tuple[str, ...]
    started_at: datetime
    ended_at: datetime
    total_files: int
    planned: int
    processed: int
    skipped: int
    errors: int
    dry_run: bool
    files: tuple[IndexFileResult, ...]


class IndexService:
    """负责扫描目录、转换文件并写入索引库。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        default_search_dirs: tuple[Path, ...] = tuple(),
        excluded_dir_keywords: tuple[str, ...] = (".assets",),
        converter_factory: ConverterFactory | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._default_search_dirs = default_search_dirs
        self._excluded_dir_keywords = tuple(
            keyword.lower() for keyword in excluded_dir_keywords if keyword.strip() != ""
        )
        self._converter_factory = converter_factory or ConverterFactory()

    def index_documents(
        self,
        request: IndexRequest,
        on_progress: Callable[[IndexProgressEvent], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> IndexResult:
        """执行目录扫描与文档索引。"""
        started_at = utc_now()
        result_items: list[IndexFileResult] = []
        total_files = 0
        planned = 0
        processed = 0
        skipped = 0
        errors = 0
        normalized_modified_since = (
            normalize_datetime_to_aware_utc(request.modified_since)
            if request.modified_since is not None
            else None
        )

        paths = self._resolve_paths(request.paths)
        normalized_file_types = self._normalize_file_types(request.file_types)

        for root_path in paths:
            self._raise_if_cancelled(should_cancel)
            scope_key = str(root_path.resolve())
            scope_started_at = utc_now()

            with self._session_factory() as session:
                document_repository = DocumentRepository(session)
                ingest_repository = IngestStateRepository(session)

                if request.dry_run:
                    run_snapshot = (
                        ingest_repository.peek_run_snapshot(request.source, scope_key)
                        if not request.force
                        else None
                    )
                    effective_since = (
                        normalized_modified_since
                        if normalized_modified_since is not None or request.force
                        else run_snapshot.cursor_updated_at
                        if run_snapshot is not None
                        else None
                    )

                    for file_path in self._iter_files(root_path, request.recursive):
                        self._raise_if_cancelled(should_cancel)
                        total_files += 1

                        if not self._matches_type_filter(file_path, normalized_file_types):
                            skipped += 1
                            self._append_result(
                                result_items,
                                on_progress,
                                IndexFileResult(
                                    file_path=str(file_path.resolve()),
                                    status="skipped",
                                    message="文件类型已被筛选条件排除",
                                    document_id=None,
                                ),
                            )
                            continue

                        file_modified_time = self._to_datetime(file_path.stat().st_mtime)
                        if effective_since is not None and file_modified_time < effective_since:
                            skipped += 1
                            self._append_result(
                                result_items,
                                on_progress,
                                IndexFileResult(
                                    file_path=str(file_path.resolve()),
                                    status="skipped",
                                    message="文件修改时间早于增量游标，已跳过",
                                    document_id=None,
                                ),
                            )
                            continue

                        existing_document = None
                        if not request.force:
                            existing_document = document_repository.get_by_path(
                                str(file_path.resolve())
                            )
                            if (
                                existing_document is not None
                                and existing_document.file_modified_time == file_modified_time
                            ):
                                skipped += 1
                                self._append_result(
                                    result_items,
                                    on_progress,
                                    IndexFileResult(
                                        file_path=str(file_path.resolve()),
                                        status="skipped",
                                        message="文件未变化，跳过重复索引",
                                        document_id=existing_document.id,
                                    ),
                                )
                                continue

                        planned += 1
                        self._append_result(
                            result_items,
                            on_progress,
                            IndexFileResult(
                                file_path=str(file_path.resolve()),
                                status="planned",
                                message=self._build_dry_run_message(
                                    file_path=file_path,
                                    existing_document=existing_document,
                                    force=request.force,
                                ),
                                document_id=existing_document.id
                                if existing_document is not None
                                else None,
                            ),
                        )

                    continue

                run_snapshot = ingest_repository.start_run(
                    request.source,
                    scope_key,
                    scope_started_at,
                    allow_legacy_replace=request.force,
                )

                scope_total = 0
                scope_processed = 0
                scope_skipped = 0
                scope_errors = 0

                try:
                    # 未显式指定时间且非强制重建时，默认沿用上次成功索引的游标。
                    effective_since = (
                        normalized_modified_since
                        if normalized_modified_since is not None or request.force
                        else run_snapshot.cursor_updated_at
                    )

                    for file_path in self._iter_files(root_path, request.recursive):
                        self._raise_if_cancelled(should_cancel)
                        scope_total += 1
                        total_files += 1

                        if not self._matches_type_filter(file_path, normalized_file_types):
                            scope_skipped += 1
                            skipped += 1
                            self._append_result(
                                result_items,
                                on_progress,
                                IndexFileResult(
                                    file_path=str(file_path.resolve()),
                                    status="skipped",
                                    message="文件类型已被筛选条件排除",
                                    document_id=None,
                                ),
                            )
                            continue

                        file_modified_time = self._to_datetime(file_path.stat().st_mtime)
                        if effective_since is not None and file_modified_time < effective_since:
                            scope_skipped += 1
                            skipped += 1
                            self._append_result(
                                result_items,
                                on_progress,
                                IndexFileResult(
                                    file_path=str(file_path.resolve()),
                                    status="skipped",
                                    message="文件修改时间早于增量游标，已跳过",
                                    document_id=None,
                                ),
                            )
                            continue

                        existing_document = None
                        if not request.force:
                            existing_document = document_repository.get_by_path(
                                str(file_path.resolve())
                            )
                            if (
                                existing_document is not None
                                and existing_document.file_modified_time == file_modified_time
                            ):
                                # 文件修改时间未变化时，直接跳过，避免重复转换与写库。
                                scope_skipped += 1
                                skipped += 1
                                self._append_result(
                                    result_items,
                                    on_progress,
                                    IndexFileResult(
                                        file_path=str(file_path.resolve()),
                                        status="skipped",
                                        message="文件未变化，跳过重复索引",
                                        document_id=existing_document.id,
                                    ),
                                )
                                continue

                        converter = self._converter_factory.create_converter(file_path)
                        conversion = converter.convert(file_path)
                        payload = self._build_upsert_payload(file_path, conversion)
                        document = document_repository.upsert(
                            payload,
                            allow_legacy_replace=request.force,
                        )

                        if conversion.status in {
                            ConversionStatus.COMPLETED,
                            ConversionStatus.FALLBACK,
                        }:
                            scope_processed += 1
                            processed += 1
                            result = IndexFileResult(
                                file_path=payload.file_path,
                                status=str(conversion.status),
                                message=(
                                    conversion.error_message or "索引成功"
                                    if conversion.status is ConversionStatus.FALLBACK
                                    else "索引成功"
                                ),
                                document_id=document.id,
                            )
                        elif conversion.status is ConversionStatus.SKIPPED:
                            scope_skipped += 1
                            skipped += 1
                            result = IndexFileResult(
                                file_path=payload.file_path,
                                status="skipped",
                                message=conversion.error_message or "文件已跳过",
                                document_id=document.id,
                            )
                        else:
                            scope_errors += 1
                            errors += 1
                            result = IndexFileResult(
                                file_path=payload.file_path,
                                status="failed",
                                message=conversion.error_message or "转换失败",
                                document_id=document.id,
                            )

                        # 单文件写入成功后立即提交，避免长目录任务结束前数据库一直不可见。
                        session.commit()
                        self._append_result(result_items, on_progress, result)

                    ingest_repository.finish_run(
                        IngestStateSummary(
                            source=request.source,
                            scope_key=scope_key,
                            started_at=scope_started_at,
                            ended_at=utc_now(),
                            last_status="success",
                            last_error_message=None,
                            cursor_updated_at=scope_started_at,
                            total_files=scope_total,
                            processed=scope_processed,
                            skipped=scope_skipped,
                            errors=scope_errors,
                        ),
                        allow_legacy_replace=request.force,
                    )
                    session.commit()
                except IndexCancelledError as exc:
                    logger.info("索引目录已取消：%s", scope_key)
                    ingest_repository.finish_run(
                        IngestStateSummary(
                            source=request.source,
                            scope_key=scope_key,
                            started_at=scope_started_at,
                            ended_at=utc_now(),
                            last_status="cancelled",
                            last_error_message=str(exc),
                            cursor_updated_at=run_snapshot.cursor_updated_at,
                            total_files=scope_total,
                            processed=scope_processed,
                            skipped=scope_skipped,
                            errors=scope_errors,
                        ),
                        allow_legacy_replace=request.force,
                    )
                    session.commit()
                    raise
                except Exception as exc:
                    session.rollback()
                    logger.exception("索引目录失败：%s", scope_key)
                    with self._session_factory() as failed_session:
                        failed_repository = IngestStateRepository(failed_session)
                        failed_repository.finish_run(
                            IngestStateSummary(
                                source=request.source,
                                scope_key=scope_key,
                                started_at=scope_started_at,
                                ended_at=utc_now(),
                                last_status="failed",
                                last_error_message=str(exc),
                                cursor_updated_at=run_snapshot.cursor_updated_at,
                                total_files=scope_total,
                                processed=scope_processed,
                                skipped=scope_skipped,
                                errors=scope_errors + 1,
                            ),
                            allow_legacy_replace=request.force,
                        )
                        failed_session.commit()
                    errors += 1
                    result = IndexFileResult(
                        file_path=scope_key,
                        status="failed",
                        message=str(exc),
                        document_id=None,
                    )
                    self._append_result(result_items, on_progress, result)

        return IndexResult(
            scopes=tuple(str(path.resolve()) for path in paths),
            started_at=started_at,
            ended_at=utc_now(),
            total_files=total_files,
            planned=planned,
            processed=processed,
            skipped=skipped,
            errors=errors,
            dry_run=request.dry_run,
            files=tuple(result_items),
        )

    def _resolve_paths(self, paths: tuple[Path, ...]) -> tuple[Path, ...]:
        """解析索引路径，必要时回退到默认目录配置。"""
        resolved_paths = paths if len(paths) > 0 else self._default_search_dirs
        if len(resolved_paths) == 0:
            raise ConfigurationError("未提供索引目录，请传入路径参数或在 .env 中配置 SEARCH_DIRS。")

        normalized: list[Path] = []
        for path in resolved_paths:
            current = path.resolve()
            if not current.exists() or not current.is_dir():
                raise ConfigurationError(f"索引目录不存在或不是文件夹：{current}")
            normalized.append(current)
        return tuple(normalized)

    def _normalize_file_types(self, file_types: tuple[str, ...]) -> tuple[str, ...]:
        """归一化文件类型筛选条件。"""
        if len(file_types) == 0:
            return SUPPORTED_FILE_EXTENSIONS
        return tuple(sorted(file_type.lower().lstrip(".") for file_type in file_types))

    def _iter_files(self, root_path: Path, recursive: bool) -> tuple[Path, ...]:
        """按递归选项列出目录中的全部文件。"""
        if self._is_excluded_directory(root_path):
            return tuple()

        if recursive:
            files: list[Path] = []
            for current_root, dir_names, file_names in os.walk(root_path):
                current_path = Path(current_root)
                dir_names[:] = [
                    directory_name
                    for directory_name in dir_names
                    if not self._is_excluded_directory(current_path / directory_name)
                ]
                for file_name in file_names:
                    files.append(current_path / file_name)
            normalized_files = tuple(files)
        else:
            normalized_files = tuple(path for path in root_path.iterdir() if path.is_file())
        return tuple(sorted(normalized_files, key=lambda item: str(item).lower()))

    def _matches_type_filter(self, file_path: Path, allowed_types: tuple[str, ...]) -> bool:
        """判断文件是否命中扩展名筛选条件。"""
        return file_path.suffix.lower().lstrip(".") in set(allowed_types)

    def _is_excluded_directory(self, directory_path: Path) -> bool:
        """判断目录名是否命中排除规则。"""

        normalized_name = directory_path.name.lower()
        return any(keyword in normalized_name for keyword in self._excluded_dir_keywords)

    def _build_upsert_payload(
        self,
        file_path: Path,
        conversion: ConversionResult,
    ) -> DocumentUpsertInput:
        """将文件元数据与转换结果组装为写库模型。"""
        stat = file_path.stat()
        return DocumentUpsertInput(
            file_name=file_path.name,
            file_type=file_path.suffix.lower().lstrip(".") or None,
            file_size=stat.st_size,
            file_created_at=self._to_datetime(stat.st_ctime),
            file_modified_time=self._to_datetime(stat.st_mtime),
            file_path=str(file_path.resolve()),
            content_markdown=conversion.content_markdown,
            conversion_type=int(conversion.conversion_type) if conversion.conversion_type else None,
            status=str(conversion.status),
            error_message=conversion.error_message,
            source="fs",
            source_url=None,
        )

    def _append_result(
        self,
        result_items: list[IndexFileResult],
        on_progress: Callable[[IndexProgressEvent], None] | None,
        result: IndexFileResult,
    ) -> None:
        """记录结果，并在需要时回调前端或 CLI 进度。"""
        result_items.append(result)
        if on_progress is not None:
            on_progress(
                IndexProgressEvent(
                    file_path=result.file_path,
                    status=result.status,
                    message=result.message,
                )
            )

    def _build_dry_run_message(
        self,
        *,
        file_path: Path,
        existing_document: object | None,
        force: bool,
    ) -> str:
        """根据当前文件状态生成 dry-run 提示。"""

        if force:
            return "命中强制重建范围，dry-run 未执行实际索引"
        if existing_document is not None:
            return "文件已存在且有更新，dry-run 未执行重新索引"
        return "文件将建立新索引，dry-run 未执行实际索引"

    def _raise_if_cancelled(self, should_cancel: Callable[[], bool] | None) -> None:
        """在任务被请求取消时中止当前索引流程。"""

        if should_cancel is not None and should_cancel():
            raise IndexCancelledError("索引任务已取消。")

    def _to_datetime(self, timestamp: float) -> datetime:
        """将文件时间戳转换为 aware UTC datetime。"""
        return aware_utc_from_timestamp(timestamp)

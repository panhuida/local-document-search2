"""文档表仓储。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, func, select, update
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from local_document_search.models import Document
from local_document_search.persistence.text_sanitizer import (
    strip_nul_bytes,
    strip_nullable_nul_bytes,
)
from local_document_search.utils import utc_now

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DocumentUpsertInput:
    """写入 documents 表所需的字段集合。"""

    file_name: str
    file_type: str | None
    file_size: int | None
    file_created_at: datetime | None
    file_modified_time: datetime | None
    file_path: str
    content_markdown: str | None
    conversion_type: int | None
    status: str
    error_message: str | None
    source: str | None
    source_url: str | None


class DocumentRepository:
    """封装 documents 表的常用查询与写入操作。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_id(self, document_id: int) -> Document | None:
        """按主键查询单条文档记录。"""
        return self._session.get(Document, document_id)

    def get_by_ids(self, document_ids: tuple[int, ...]) -> list[Document]:
        """按主键集合批量查询文档记录。"""
        if len(document_ids) == 0:
            return []
        statement = (
            select(Document).where(Document.id.in_(document_ids)).order_by(Document.id.asc())
        )
        return list(self._session.scalars(statement))

    def get_by_path(self, file_path: str) -> Document | None:
        """按绝对路径查询文档记录。"""
        statement = select(Document).where(Document.file_path == file_path)
        return self._session.scalars(statement).first()

    def get_id_by_path(self, file_path: str) -> int | None:
        """按绝对路径查询文档主键，避免加载整行历史数据。"""
        statement = select(Document.id).where(Document.file_path == file_path)
        return self._session.execute(statement).scalar_one_or_none()

    def list_failed_documents(
        self,
        *,
        include_fallback: bool = False,
        file_name_keyword: str | None = None,
        updated_after: datetime | None = None,
        updated_before: datetime | None = None,
    ) -> list[Document]:
        """列出失败文档，必要时一并包含回退为元数据索引的文档。"""
        retryable_statuses = ("failed", "fallback") if include_fallback else ("failed",)
        conditions: list[ColumnElement[bool]] = [Document.status.in_(retryable_statuses)]
        if file_name_keyword:
            conditions.append(Document.file_name.ilike(f"%{file_name_keyword}%"))
        if updated_after:
            conditions.append(Document.updated_at >= updated_after)
        if updated_before:
            conditions.append(Document.updated_at <= updated_before)

        statement = select(Document).where(and_(*conditions)).order_by(Document.updated_at.desc())
        return list(self._session.scalars(statement))

    def list_documents_under_scope(self, scope_path: str | None) -> list[Document]:
        """列出指定路径范围内的全部文档记录。"""
        statement = select(Document)
        if scope_path:
            # 仓储层先做前缀粗筛，Service 层再做严格路径边界校验。
            statement = statement.where(Document.file_path.like(f"{scope_path}%"))
        statement = statement.order_by(Document.file_path.asc())
        return list(self._session.scalars(statement))

    def count_all(self) -> int:
        """统计 documents 表总记录数。"""
        statement = select(func.count()).select_from(Document)
        result = self._session.execute(statement).scalar_one()
        return int(result)

    def delete_by_ids(self, document_ids: tuple[int, ...]) -> int:
        """按主键批量删除文档记录。"""
        if len(document_ids) == 0:
            return 0
        documents = self.get_by_ids(document_ids)
        for document in documents:
            self._session.delete(document)
        self._session.flush()
        return len(documents)

    def upsert(
        self,
        payload: DocumentUpsertInput,
        *,
        allow_legacy_replace: bool = False,
    ) -> Document:
        """按文件路径执行插入或更新。"""
        sanitized_payload, sanitized_fields = self._sanitize_payload(payload)
        if sanitized_fields > 0:
            logger.warning(
                "文档记录包含 NUL 字节，写库前已清洗：%s，受影响字段数：%s",
                sanitized_payload.file_path,
                sanitized_fields,
            )
        if allow_legacy_replace:
            return self._upsert_without_loading(sanitized_payload)

        document = self.get_by_path(sanitized_payload.file_path)
        if document is None:
            document = Document(file_path=sanitized_payload.file_path)
            self._session.add(document)

        document.file_name = sanitized_payload.file_name
        document.file_type = sanitized_payload.file_type
        document.file_size = sanitized_payload.file_size
        document.file_created_at = sanitized_payload.file_created_at
        document.file_modified_time = sanitized_payload.file_modified_time
        document.content_markdown = sanitized_payload.content_markdown
        document.conversion_type = sanitized_payload.conversion_type
        document.status = sanitized_payload.status
        document.error_message = sanitized_payload.error_message
        document.source = sanitized_payload.source
        document.source_url = sanitized_payload.source_url

        self._session.flush()
        return document

    def _upsert_without_loading(self, payload: DocumentUpsertInput) -> Document:
        """在强制重建时按路径直接覆盖旧记录，避免读取历史脏时间字段。"""

        document_id = self.get_id_by_path(payload.file_path)
        current_time = utc_now()
        values = {
            "file_name": payload.file_name,
            "file_type": payload.file_type,
            "file_size": payload.file_size,
            "file_created_at": payload.file_created_at,
            "file_modified_time": payload.file_modified_time,
            "file_path": payload.file_path,
            "content_markdown": payload.content_markdown,
            "conversion_type": payload.conversion_type,
            "status": payload.status,
            "error_message": payload.error_message,
            "source": payload.source,
            "source_url": payload.source_url,
            # 强制重建用于修复旧格式时间数据，直接把记录时间一并刷新为新规范。
            "created_at": current_time,
            "updated_at": current_time,
        }

        if document_id is None:
            document = Document(**values)
            self._session.add(document)
            self._session.flush()
            return document

        self._session.execute(update(Document).where(Document.id == document_id).values(**values))
        self._session.flush()
        repaired_document = self.get_by_id(document_id)
        if repaired_document is None:
            raise RuntimeError(f"按路径更新文档后无法重新读取：{payload.file_path}")
        return repaired_document

    def _sanitize_payload(self, payload: DocumentUpsertInput) -> tuple[DocumentUpsertInput, int]:
        """清洗待写入文档中的非法文本字符。"""

        file_name, file_name_changed = strip_nul_bytes(payload.file_name)
        file_type, file_type_changed = strip_nullable_nul_bytes(payload.file_type)
        file_path, file_path_changed = strip_nul_bytes(payload.file_path)
        content_markdown, content_markdown_changed = strip_nullable_nul_bytes(
            payload.content_markdown
        )
        status, status_changed = strip_nul_bytes(payload.status)
        error_message, error_message_changed = strip_nullable_nul_bytes(payload.error_message)
        source, source_changed = strip_nullable_nul_bytes(payload.source)
        source_url, source_url_changed = strip_nullable_nul_bytes(payload.source_url)

        sanitized_fields = sum(
            (
                file_name_changed,
                file_type_changed,
                file_path_changed,
                content_markdown_changed,
                status_changed,
                error_message_changed,
                source_changed,
                source_url_changed,
            )
        )
        return (
            DocumentUpsertInput(
                file_name=file_name,
                file_type=file_type,
                file_size=payload.file_size,
                file_created_at=payload.file_created_at,
                file_modified_time=payload.file_modified_time,
                file_path=file_path,
                content_markdown=content_markdown,
                conversion_type=payload.conversion_type,
                status=status,
                error_message=error_message,
                source=source,
                source_url=source_url,
            ),
            sanitized_fields,
        )

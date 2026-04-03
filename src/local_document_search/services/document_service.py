"""文档预览服务。"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from local_document_search.exceptions import DocumentNotFoundError
from local_document_search.persistence.repositories import DocumentRepository
from local_document_search.utils.markdown_renderer import render_markdown_to_html

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DocumentPreviewRequest:
    """文档预览请求。"""

    document_id: int


@dataclass(frozen=True)
class DocumentPreviewResult:
    """文档预览结果。"""

    document_id: int
    file_name: str
    file_path: str
    status: str
    content_markdown: str
    content_html: str


class DocumentService:
    """负责将已索引文档渲染为可预览内容。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def preview_document(self, request: DocumentPreviewRequest) -> DocumentPreviewResult:
        """读取文档内容并生成 HTML 预览。"""
        with self._session_factory() as session:
            repository = DocumentRepository(session)
            document = repository.get_by_id(request.document_id)
            if document is None:
                raise DocumentNotFoundError(f"文档不存在：{request.document_id}")

            content_markdown = document.content_markdown or ""
            # 预览页统一复用 markdown-it-py 渲染器，保证表格等结构的输出一致。
            content_html = render_markdown_to_html(content_markdown)
            return DocumentPreviewResult(
                document_id=document.id,
                file_name=document.file_name,
                file_path=document.file_path,
                status=document.status,
                content_markdown=content_markdown,
                content_html=content_html,
            )

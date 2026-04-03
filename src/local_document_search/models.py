"""SQLAlchemy ORM 模型定义。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from local_document_search.persistence.database import Base, UTCDateTime
from local_document_search.utils import utc_now


class IngestState(Base):
    """记录目录级索引任务的最近一次执行状态。"""

    __tablename__ = "ingest_state"
    __table_args__ = (
        UniqueConstraint("source", "scope_key", name="idx_ingest_state_source_scope"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    scope_key: Mapped[str] = mapped_column(Text, nullable=False)
    last_started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    cursor_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    total_files: Mapped[int | None] = mapped_column(Integer, nullable=True)
    processed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    skipped: Mapped[int | None] = mapped_column(Integer, nullable=True)
    errors: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(10), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class Document(Base):
    """保存单个文件的索引结果与转换状态。"""

    __tablename__ = "documents"
    __table_args__ = (Index("idx_documents_file_path", "file_path", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_name: Mapped[str] = mapped_column(String(200), nullable=False)
    file_type: Mapped[str | None] = mapped_column(String(10), nullable=True)
    file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    file_created_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    file_modified_time: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    file_path: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    content_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    conversion_type: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="pending", nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str | None] = mapped_column(String(30), default="fs", nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

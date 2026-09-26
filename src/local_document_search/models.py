"""SQLAlchemy ORM 模型定义。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from local_document_search.persistence.database import Base, UTCDateTime
from local_document_search.utils import utc_now


class IngestState(Base):
    """记录某个来源在特定作用域下最近一次导入状态。"""

    __tablename__ = "ingest_state"
    __table_args__ = (
        UniqueConstraint("source", "scope_key", name="idx_ingest_state_source_scope"),
        {
            "comment": (
                "同步状态表：记录来源在特定作用域下最近一次导入状态，用于增量导入、"
                "任务统计和错误诊断。"
            )
        },
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
        comment="主键，自增 ID。",
    )
    source: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        comment="数据来源标识，如本地文件系统 fs 或 Joplin。",
    )
    scope_key: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="来源作用域唯一键，本地文件导入通常使用目录路径。",
    )
    last_started_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
        comment="最近一次导入开始时间。",
    )
    last_ended_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
        comment="最近一次导入结束时间。",
    )
    last_error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="最近一次导入任务级错误信息，单文件错误写入 documents.error_message。",
    )
    cursor_updated_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
        comment="增量导入游标时间，未显式传 date_from 时用于下次扫描起点。",
    )
    total_files: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="最近一次导入扫描到的文件总数。",
    )
    processed: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="最近一次导入成功处理并写库的文件数。",
    )
    skipped: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="最近一次导入中因未变化、过滤条件等原因跳过的文件数。",
    )
    errors: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="最近一次导入中失败的文件数。",
    )
    last_status: Mapped[str | None] = mapped_column(
        String(10),
        nullable=True,
        comment="最近一次导入状态：running、success、failed、cancelled。",
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        default=utc_now,
        nullable=False,
        comment="记录创建时间。",
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
        comment="记录更新时间。",
    )


class Document(Base):
    """保存单个文档或媒体文件的标准化索引记录。"""

    __tablename__ = "documents"
    __table_args__ = (
        Index("idx_documents_file_path", "file_path", unique=True),
        {
            "comment": (
                "文档记录表：保存单个文档或媒体文件的标准化索引记录，是搜索、预览、"
                "失败重试和打开原文件的核心数据表。"
            )
        },
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
        comment="主键，自增 ID。",
    )
    file_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        comment="文件名，含扩展名，用于列表展示和文件名检索。",
    )
    file_type: Mapped[str | None] = mapped_column(
        String(10),
        nullable=True,
        comment="文件扩展名或归一化类型，如 md、pdf、png。",
    )
    file_size: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        comment="文件大小，单位字节。",
    )
    file_created_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
        comment="文件创建时间。",
    )
    file_modified_time: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
        comment="文件最后修改时间，增量跳过逻辑依赖该值。",
    )
    file_path: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        unique=True,
        comment="文件标准化绝对路径，全库唯一。",
    )
    content_markdown: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="转换后的 Markdown 内容，失败记录可为空。",
    )
    conversion_type: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment=(
            "转换方式枚举值：1=DIRECT，2=STRUCTURED_TO_MD，3=XMIND_TO_MD，"
            "4=DRAWIO_TO_MD，5=HTML_TO_MD，6=IMAGE_METADATA_TO_MD，"
            "7=VIDEO_METADATA_TO_MD。"
        ),
    )
    status: Mapped[str] = mapped_column(
        String(10),
        default="pending",
        nullable=False,
        comment="转换状态：pending、completed、failed、skipped。",
    )
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="单文件转换失败原因，成功时通常为空。",
    )
    source: Mapped[str | None] = mapped_column(
        String(30),
        default="fs",
        nullable=True,
        comment="数据来源标识，用于区分本地文件、Joplin 或未来扩展来源。",
    )
    source_url: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="来源 URL，本地文件通常为空，外部来源可保存原始链接。",
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        default=utc_now,
        nullable=False,
        comment="记录创建时间。",
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
        comment="记录更新时间。",
    )

"""转换器抽象基类与通用结果对象。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from pathlib import Path


class ConversionType(IntEnum):
    """文档转换类型枚举，与数据库中的 conversion_type 对应。"""

    DIRECT = 1
    STRUCTURED_TO_MD = 2
    XMIND_TO_MD = 3
    DRAWIO_TO_MD = 4
    HTML_TO_MD = 5
    IMAGE_METADATA_TO_MD = 6
    VIDEO_METADATA_TO_MD = 7


class ConversionStatus(StrEnum):
    """单文件转换结果状态。"""

    COMPLETED = "completed"
    FALLBACK = "fallback"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class ConversionResult:
    """转换器统一返回的结构化结果。"""

    content_markdown: str | None
    conversion_type: ConversionType | None
    status: ConversionStatus
    error_message: str | None


class BaseConverter(ABC):
    """所有文件转换器都必须实现的抽象接口。"""

    @abstractmethod
    def convert(self, source_path: Path) -> ConversionResult:
        raise NotImplementedError

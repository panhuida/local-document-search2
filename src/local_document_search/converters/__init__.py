"""转换器工厂与支持的文件类型映射。"""

from __future__ import annotations

from pathlib import Path

from local_document_search.config import LargeFileIndexMode
from local_document_search.converters.base import BaseConverter
from local_document_search.converters.drawio import DrawioConverter
from local_document_search.converters.markitdown import (
    DirectTextConverter,
    HtmlConverter,
    LegacyBinaryOfficeConverter,
    MarkItDownConverter,
    MediaMetadataConverter,
)
from local_document_search.converters.pdf import PdfConverter
from local_document_search.converters.xmind import XMindConverter
from local_document_search.exceptions import UnsupportedFileTypeError
from local_document_search.file_types.registry import (
    SUPPORTED_FILE_EXTENSIONS,
    FileTypeHandlerKind,
    get_file_type_definition,
)


class ConverterFactory:
    """根据文件扩展名分派具体转换器。"""

    def __init__(
        self,
        *,
        markitdown_timeout_seconds: int = 90,
        large_file_threshold_mb: int = 15,
        very_large_file_threshold_mb: int = 50,
        large_file_index_mode: LargeFileIndexMode = LargeFileIndexMode.METADATA,
    ) -> None:
        markitdown_converter = MarkItDownConverter(
            timeout_seconds=markitdown_timeout_seconds,
            large_file_threshold_mb=large_file_threshold_mb,
            very_large_file_threshold_mb=very_large_file_threshold_mb,
            large_file_index_mode=large_file_index_mode,
        )
        pdf_converter = PdfConverter(
            markitdown_converter=markitdown_converter,
            very_large_file_threshold_mb=very_large_file_threshold_mb,
            large_file_index_mode=large_file_index_mode,
        )
        self._converter_map: dict[FileTypeHandlerKind, BaseConverter] = {
            FileTypeHandlerKind.DIRECT_TEXT: DirectTextConverter(),
            FileTypeHandlerKind.HTML: HtmlConverter(),
            FileTypeHandlerKind.PDF: pdf_converter,
            FileTypeHandlerKind.MARKITDOWN: markitdown_converter,
            FileTypeHandlerKind.LEGACY_BINARY_OFFICE: LegacyBinaryOfficeConverter(
                structured_converter=markitdown_converter
            ),
            FileTypeHandlerKind.XMIND: XMindConverter(),
            FileTypeHandlerKind.DRAWIO: DrawioConverter(),
            FileTypeHandlerKind.IMAGE_METADATA: MediaMetadataConverter(is_video=False),
            FileTypeHandlerKind.VIDEO_METADATA: MediaMetadataConverter(is_video=True),
        }

    def create_converter(self, source_path: Path) -> BaseConverter:
        """为给定文件选择合适的转换器。"""

        extension = self.normalize_extension(source_path)
        definition = get_file_type_definition(extension)
        if definition is None:
            raise UnsupportedFileTypeError(f"不支持的文件类型：{source_path.suffix}")
        converter = self._converter_map.get(definition.handler_kind)
        if converter is None:
            raise UnsupportedFileTypeError(f"文件类型缺少对应转换器：{source_path.suffix}")
        return converter

    def is_supported(self, source_path: Path) -> bool:
        """判断文件类型是否在当前版本支持范围内。"""

        return self.normalize_extension(source_path) in SUPPORTED_FILE_EXTENSIONS

    @staticmethod
    def normalize_extension(source_path: Path) -> str:
        """统一把扩展名归一化为小写且不带点的形式。"""

        return source_path.suffix.lower().lstrip(".")

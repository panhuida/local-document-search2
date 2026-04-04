"""文件类型注册表，负责维护扩展名与转换处理方式。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class FileTypeHandlerKind(StrEnum):
    """文件类型对应的转换处理方式。"""

    DIRECT_TEXT = "direct_text"
    HTML = "html"
    PDF = "pdf"
    MARKITDOWN = "markitdown"
    LEGACY_BINARY_OFFICE = "legacy_binary_office"
    XMIND = "xmind"
    DRAWIO = "drawio"
    IMAGE_METADATA = "image_metadata"
    VIDEO_METADATA = "video_metadata"


@dataclass(frozen=True)
class FileTypeDefinition:
    """单个文件类型的领域注册信息。"""

    extension: str
    handler_kind: FileTypeHandlerKind


FILE_TYPE_DEFINITIONS: tuple[FileTypeDefinition, ...] = (
    FileTypeDefinition("pdf", FileTypeHandlerKind.PDF),
    FileTypeDefinition("docx", FileTypeHandlerKind.MARKITDOWN),
    FileTypeDefinition("xlsx", FileTypeHandlerKind.MARKITDOWN),
    FileTypeDefinition("pptx", FileTypeHandlerKind.MARKITDOWN),
    FileTypeDefinition("doc", FileTypeHandlerKind.LEGACY_BINARY_OFFICE),
    FileTypeDefinition("xls", FileTypeHandlerKind.LEGACY_BINARY_OFFICE),
    FileTypeDefinition("ppt", FileTypeHandlerKind.LEGACY_BINARY_OFFICE),
    FileTypeDefinition("md", FileTypeHandlerKind.DIRECT_TEXT),
    FileTypeDefinition("txt", FileTypeHandlerKind.DIRECT_TEXT),
    FileTypeDefinition("html", FileTypeHandlerKind.HTML),
    FileTypeDefinition("htm", FileTypeHandlerKind.HTML),
    FileTypeDefinition("xmind", FileTypeHandlerKind.XMIND),
    FileTypeDefinition("drawio", FileTypeHandlerKind.DRAWIO),
    FileTypeDefinition("jpg", FileTypeHandlerKind.IMAGE_METADATA),
    FileTypeDefinition("jpeg", FileTypeHandlerKind.IMAGE_METADATA),
    FileTypeDefinition("png", FileTypeHandlerKind.IMAGE_METADATA),
    FileTypeDefinition("gif", FileTypeHandlerKind.IMAGE_METADATA),
    FileTypeDefinition("webp", FileTypeHandlerKind.IMAGE_METADATA),
    FileTypeDefinition("svg", FileTypeHandlerKind.IMAGE_METADATA),
    FileTypeDefinition("mp4", FileTypeHandlerKind.VIDEO_METADATA),
    FileTypeDefinition("mov", FileTypeHandlerKind.VIDEO_METADATA),
    FileTypeDefinition("avi", FileTypeHandlerKind.VIDEO_METADATA),
    FileTypeDefinition("mkv", FileTypeHandlerKind.VIDEO_METADATA),
    FileTypeDefinition("flv", FileTypeHandlerKind.VIDEO_METADATA),
)

SUPPORTED_FILE_EXTENSIONS: tuple[str, ...] = tuple(
    definition.extension for definition in FILE_TYPE_DEFINITIONS
)
_FILE_TYPE_DEFINITION_MAP: dict[str, FileTypeDefinition] = {
    definition.extension: definition for definition in FILE_TYPE_DEFINITIONS
}

if len(_FILE_TYPE_DEFINITION_MAP) != len(FILE_TYPE_DEFINITIONS):
    raise RuntimeError("文件类型注册表中存在重复扩展名，请检查 FILE_TYPE_DEFINITIONS。")


def normalize_file_type_extension(extension: str) -> str:
    """把扩展名归一化为小写且不带点。"""

    return extension.lower().lstrip(".")


def get_file_type_definition(extension: str) -> FileTypeDefinition | None:
    """按扩展名获取文件类型注册信息。"""

    return _FILE_TYPE_DEFINITION_MAP.get(normalize_file_type_extension(extension))

"""统一维护文件类型支持、展示名称与页面分组。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class FileTypeHandlerKind(StrEnum):
    """文件类型对应的转换处理方式。"""

    DIRECT_TEXT = "direct_text"
    HTML = "html"
    MARKITDOWN = "markitdown"
    LEGACY_BINARY_OFFICE = "legacy_binary_office"
    XMIND = "xmind"
    DRAWIO = "drawio"
    IMAGE_METADATA = "image_metadata"
    VIDEO_METADATA = "video_metadata"


@dataclass(frozen=True)
class FileTypeDefinition:
    """单个文件类型的统一注册信息。"""

    extension: str
    label: str
    group: str
    handler_kind: FileTypeHandlerKind


@dataclass(frozen=True)
class FileTypeOptionView:
    """页面展示用的文件类型选项。"""

    value: str
    label: str


@dataclass(frozen=True)
class FileTypeGroupView:
    """页面展示用的文件类型分组。"""

    title: str
    options: tuple[FileTypeOptionView, ...]


FILE_TYPE_DEFINITIONS: tuple[FileTypeDefinition, ...] = (
    FileTypeDefinition("pdf", "PDF (.pdf)", "Office / PDF", FileTypeHandlerKind.MARKITDOWN),
    FileTypeDefinition("docx", "Word (.docx)", "Office / PDF", FileTypeHandlerKind.MARKITDOWN),
    FileTypeDefinition("xlsx", "Excel (.xlsx)", "Office / PDF", FileTypeHandlerKind.MARKITDOWN),
    FileTypeDefinition(
        "pptx", "PowerPoint (.pptx)", "Office / PDF", FileTypeHandlerKind.MARKITDOWN
    ),
    FileTypeDefinition(
        "doc", "旧版 Word (.doc)", "Office / PDF", FileTypeHandlerKind.LEGACY_BINARY_OFFICE
    ),
    FileTypeDefinition(
        "xls", "旧版 Excel (.xls)", "Office / PDF", FileTypeHandlerKind.LEGACY_BINARY_OFFICE
    ),
    FileTypeDefinition(
        "ppt",
        "旧版 PowerPoint (.ppt)",
        "Office / PDF",
        FileTypeHandlerKind.LEGACY_BINARY_OFFICE,
    ),
    FileTypeDefinition("md", "Markdown (.md)", "Markdown / 文本", FileTypeHandlerKind.DIRECT_TEXT),
    FileTypeDefinition("txt", "文本 (.txt)", "Markdown / 文本", FileTypeHandlerKind.DIRECT_TEXT),
    FileTypeDefinition("html", "HTML (.html)", "网页", FileTypeHandlerKind.HTML),
    FileTypeDefinition("htm", "HTML (.htm)", "网页", FileTypeHandlerKind.HTML),
    FileTypeDefinition("xmind", "XMind (.xmind)", "思维导图", FileTypeHandlerKind.XMIND),
    FileTypeDefinition("drawio", "draw.io (.drawio)", "流程图", FileTypeHandlerKind.DRAWIO),
    FileTypeDefinition("jpg", "JPG (.jpg)", "图片", FileTypeHandlerKind.IMAGE_METADATA),
    FileTypeDefinition("jpeg", "JPEG (.jpeg)", "图片", FileTypeHandlerKind.IMAGE_METADATA),
    FileTypeDefinition("png", "PNG (.png)", "图片", FileTypeHandlerKind.IMAGE_METADATA),
    FileTypeDefinition("gif", "GIF (.gif)", "图片", FileTypeHandlerKind.IMAGE_METADATA),
    FileTypeDefinition("webp", "WebP (.webp)", "图片", FileTypeHandlerKind.IMAGE_METADATA),
    FileTypeDefinition("svg", "SVG (.svg)", "图片", FileTypeHandlerKind.IMAGE_METADATA),
    FileTypeDefinition("mp4", "MP4 (.mp4)", "视频", FileTypeHandlerKind.VIDEO_METADATA),
    FileTypeDefinition("mov", "MOV (.mov)", "视频", FileTypeHandlerKind.VIDEO_METADATA),
    FileTypeDefinition("avi", "AVI (.avi)", "视频", FileTypeHandlerKind.VIDEO_METADATA),
    FileTypeDefinition("mkv", "MKV (.mkv)", "视频", FileTypeHandlerKind.VIDEO_METADATA),
    FileTypeDefinition("flv", "FLV (.flv)", "视频", FileTypeHandlerKind.VIDEO_METADATA),
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


def build_file_type_groups(
    supported_file_types: tuple[str, ...] = SUPPORTED_FILE_EXTENSIONS,
) -> tuple[FileTypeGroupView, ...]:
    """按注册顺序构造页面展示用的文件类型分组。"""

    normalized_supported = {
        normalize_file_type_extension(file_type) for file_type in supported_file_types
    }
    grouped_options: dict[str, list[FileTypeOptionView]] = {}
    group_order: list[str] = []

    for definition in FILE_TYPE_DEFINITIONS:
        if definition.extension not in normalized_supported:
            continue
        if definition.group not in grouped_options:
            grouped_options[definition.group] = []
            group_order.append(definition.group)
        grouped_options[definition.group].append(
            FileTypeOptionView(value=definition.extension, label=definition.label)
        )

    return tuple(
        FileTypeGroupView(title=group_title, options=tuple(grouped_options[group_title]))
        for group_title in group_order
    )

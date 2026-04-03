"""文件类型展示定义，负责页面标签、分组与顺序。"""

from __future__ import annotations

from dataclasses import dataclass

from local_document_search.file_types.registry import (
    FILE_TYPE_DEFINITIONS,
    SUPPORTED_FILE_EXTENSIONS,
    normalize_file_type_extension,
)


@dataclass(frozen=True)
class FileTypePresentationDefinition:
    """单个文件类型的展示注册信息。"""

    extension: str
    label: str
    group: str


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


FILE_TYPE_PRESENTATIONS: tuple[FileTypePresentationDefinition, ...] = (
    FileTypePresentationDefinition("pdf", "PDF (.pdf)", "Office / PDF"),
    FileTypePresentationDefinition("docx", "Word (.docx)", "Office / PDF"),
    FileTypePresentationDefinition("xlsx", "Excel (.xlsx)", "Office / PDF"),
    FileTypePresentationDefinition("pptx", "PowerPoint (.pptx)", "Office / PDF"),
    FileTypePresentationDefinition("doc", "旧版 Word (.doc)", "Office / PDF"),
    FileTypePresentationDefinition("xls", "旧版 Excel (.xls)", "Office / PDF"),
    FileTypePresentationDefinition("ppt", "旧版 PowerPoint (.ppt)", "Office / PDF"),
    FileTypePresentationDefinition("md", "Markdown (.md)", "Markdown / 文本"),
    FileTypePresentationDefinition("txt", "文本 (.txt)", "Markdown / 文本"),
    FileTypePresentationDefinition("html", "HTML (.html)", "网页"),
    FileTypePresentationDefinition("htm", "HTML (.htm)", "网页"),
    FileTypePresentationDefinition("xmind", "XMind (.xmind)", "思维导图"),
    FileTypePresentationDefinition("drawio", "draw.io (.drawio)", "流程图"),
    FileTypePresentationDefinition("jpg", "JPG (.jpg)", "图片"),
    FileTypePresentationDefinition("jpeg", "JPEG (.jpeg)", "图片"),
    FileTypePresentationDefinition("png", "PNG (.png)", "图片"),
    FileTypePresentationDefinition("gif", "GIF (.gif)", "图片"),
    FileTypePresentationDefinition("webp", "WebP (.webp)", "图片"),
    FileTypePresentationDefinition("svg", "SVG (.svg)", "图片"),
    FileTypePresentationDefinition("mp4", "MP4 (.mp4)", "视频"),
    FileTypePresentationDefinition("mov", "MOV (.mov)", "视频"),
    FileTypePresentationDefinition("avi", "AVI (.avi)", "视频"),
    FileTypePresentationDefinition("mkv", "MKV (.mkv)", "视频"),
    FileTypePresentationDefinition("flv", "FLV (.flv)", "视频"),
)

_FILE_TYPE_PRESENTATION_MAP: dict[str, FileTypePresentationDefinition] = {
    definition.extension: definition for definition in FILE_TYPE_PRESENTATIONS
}

if len(_FILE_TYPE_PRESENTATION_MAP) != len(FILE_TYPE_PRESENTATIONS):
    raise RuntimeError("文件类型展示定义中存在重复扩展名，请检查 FILE_TYPE_PRESENTATIONS。")

_REGISTERED_EXTENSIONS = {definition.extension for definition in FILE_TYPE_DEFINITIONS}
_PRESENTED_EXTENSIONS = {definition.extension for definition in FILE_TYPE_PRESENTATIONS}
if _REGISTERED_EXTENSIONS != _PRESENTED_EXTENSIONS:
    raise RuntimeError("文件类型展示定义与注册表不一致，请同步更新两层配置。")


def get_file_type_presentation_definition(
    extension: str,
) -> FileTypePresentationDefinition | None:
    """按扩展名获取文件类型展示定义。"""

    return _FILE_TYPE_PRESENTATION_MAP.get(normalize_file_type_extension(extension))


def build_file_type_groups(
    supported_file_types: tuple[str, ...] = SUPPORTED_FILE_EXTENSIONS,
) -> tuple[FileTypeGroupView, ...]:
    """按展示注册顺序构造页面文件类型分组。"""

    normalized_supported = {
        normalize_file_type_extension(file_type) for file_type in supported_file_types
    }
    grouped_options: dict[str, list[FileTypeOptionView]] = {}
    group_order: list[str] = []

    for definition in FILE_TYPE_PRESENTATIONS:
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

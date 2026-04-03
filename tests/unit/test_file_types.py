"""文件类型注册表与展示层测试。"""

from __future__ import annotations

from local_document_search.file_types import (
    FILE_TYPE_DEFINITIONS,
    FILE_TYPE_PRESENTATIONS,
    SUPPORTED_FILE_EXTENSIONS,
    FileTypeHandlerKind,
    build_file_type_groups,
    get_file_type_definition,
    get_file_type_presentation_definition,
)


def test_file_type_registry_and_presentation_cover_same_extensions() -> None:
    """展示层与注册表应覆盖相同的扩展名集合。"""

    registry_extensions = {definition.extension for definition in FILE_TYPE_DEFINITIONS}
    presentation_extensions = {definition.extension for definition in FILE_TYPE_PRESENTATIONS}

    assert registry_extensions == presentation_extensions
    assert registry_extensions == set(SUPPORTED_FILE_EXTENSIONS)


def test_get_file_type_definition_returns_registry_definition() -> None:
    """注册表查询应返回规范化后的处理器定义。"""

    definition = get_file_type_definition(".drawio")

    assert definition is not None
    assert definition.extension == "drawio"
    assert definition.handler_kind is FileTypeHandlerKind.DRAWIO


def test_get_file_type_presentation_definition_returns_view_metadata() -> None:
    """展示查询应返回标签与分组信息。"""

    definition = get_file_type_presentation_definition("DOCX")

    assert definition is not None
    assert definition.extension == "docx"
    assert definition.label == "Word (.docx)"
    assert definition.group == "Office / PDF"


def test_build_file_type_groups_preserves_registered_display_order() -> None:
    """页面分组应按展示层定义的顺序输出。"""

    groups = build_file_type_groups(("drawio", "xmind", "pdf", "flv"))

    assert tuple(group.title for group in groups) == ("Office / PDF", "思维导图", "流程图", "视频")
    assert groups[0].options[0].value == "pdf"
    assert groups[1].options[0].value == "xmind"
    assert groups[2].options[0].value == "drawio"
    assert groups[3].options[0].value == "flv"

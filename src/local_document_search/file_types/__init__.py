"""统一导出文件类型注册表与展示层接口。"""

from local_document_search.file_types.presentation import (
    FILE_TYPE_PRESENTATIONS,
    FileTypeGroupView,
    FileTypeOptionView,
    FileTypePresentationDefinition,
    build_file_type_groups,
    get_file_type_presentation_definition,
)
from local_document_search.file_types.registry import (
    FILE_TYPE_DEFINITIONS,
    SUPPORTED_FILE_EXTENSIONS,
    FileTypeDefinition,
    FileTypeHandlerKind,
    get_file_type_definition,
    normalize_file_type_extension,
)

__all__ = [
    "FILE_TYPE_DEFINITIONS",
    "FILE_TYPE_PRESENTATIONS",
    "SUPPORTED_FILE_EXTENSIONS",
    "FileTypeDefinition",
    "FileTypeGroupView",
    "FileTypeHandlerKind",
    "FileTypeOptionView",
    "FileTypePresentationDefinition",
    "build_file_type_groups",
    "get_file_type_definition",
    "get_file_type_presentation_definition",
    "normalize_file_type_extension",
]

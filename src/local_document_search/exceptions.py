"""项目内统一使用的自定义异常定义。"""

from __future__ import annotations


class LocalDocumentSearchError(Exception):
    """项目级基础异常。"""


class ConfigurationError(LocalDocumentSearchError):
    """配置加载异常。"""


class DatabaseInitializationError(LocalDocumentSearchError):
    """数据库初始化异常。"""


class UnsupportedBackendError(LocalDocumentSearchError):
    """数据库后端暂未实现。"""


class UnsupportedFileTypeError(LocalDocumentSearchError):
    """文件类型不受支持。"""


class DocumentNotFoundError(LocalDocumentSearchError):
    """文档不存在。"""


class FileOpenError(LocalDocumentSearchError):
    """打开文件失败。"""


class IndexCancelledError(LocalDocumentSearchError):
    """索引任务被用户取消。"""

"""搜索后端抽象定义。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class SearchBackendRequest:
    """搜索后端统一请求模型。"""

    query: str
    limit: int
    offset: int


@dataclass(frozen=True)
class SearchBackendHit:
    """搜索后端返回的单条命中记录。"""

    document_id: int
    file_name: str
    file_path: str
    file_type: str | None
    snippet: str
    score: float | None
    modified_at: datetime | None


@dataclass(frozen=True)
class SearchBackendResult:
    """搜索后端统一响应模型。"""

    total: int
    hits: tuple[SearchBackendHit, ...]


class SearchBackend(ABC):
    """搜索后端抽象基类。"""

    @abstractmethod
    def search(self, request: SearchBackendRequest) -> SearchBackendResult:
        raise NotImplementedError

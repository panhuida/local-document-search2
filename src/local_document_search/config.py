"""应用配置加载与运行目录准备。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from dotenv import load_dotenv

from local_document_search.exceptions import ConfigurationError


class DatabaseBackend(StrEnum):
    """支持的数据库后端枚举。"""

    SQLITE = "sqlite"
    POSTGRESQL = "postgresql"


class SearchMode(StrEnum):
    """搜索请求支持的匹配模式。"""

    FULLTEXT = "fulltext"
    FUZZY = "fuzzy"


class LargeFileIndexMode(StrEnum):
    """超大结构化文档的索引策略。"""

    FULL = "full"
    METADATA = "metadata"


@dataclass(frozen=True)
class AppConfig:
    """应用运行期配置对象。"""

    database_backend: DatabaseBackend
    postgresql_default_search_mode: SearchMode
    sqlite_db_path: Path
    database_url: str | None
    search_dirs: tuple[Path, ...]
    excluded_dir_keywords: tuple[str, ...]
    flask_host: str
    flask_port: int
    flask_debug: bool
    log_level: str
    markitdown_timeout_seconds: int
    large_file_threshold_mb: int
    very_large_file_threshold_mb: int
    large_file_index_mode: LargeFileIndexMode
    project_root: Path


_CONFIG_CACHE: AppConfig | None = None


def _parse_bool(raw_value: str | None, default: bool) -> bool:
    """把环境变量中的布尔值解析为 Python bool。"""

    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_positive_int(raw_value: str | None, default: int, config_name: str) -> int:
    """把环境变量中的正整数解析为 Python int。"""

    if raw_value is None or raw_value.strip() == "":
        return default

    try:
        parsed_value = int(raw_value)
    except ValueError as exc:
        raise ConfigurationError(f"{config_name} 必须是正整数：{raw_value}") from exc

    if parsed_value <= 0:
        raise ConfigurationError(f"{config_name} 必须大于 0：{raw_value}")
    return parsed_value


def _parse_large_file_index_mode(raw_value: str | None) -> LargeFileIndexMode:
    """解析超大文件索引策略。"""

    normalized_value = (raw_value or LargeFileIndexMode.METADATA.value).strip().lower()
    try:
        return LargeFileIndexMode(normalized_value)
    except ValueError as exc:
        raise ConfigurationError("LARGE_FILE_INDEX_MODE 仅支持 full 或 metadata。") from exc


def _parse_search_mode(raw_value: str | None, *, config_name: str) -> SearchMode:
    """解析搜索模式。"""

    normalized_value = (raw_value or SearchMode.FULLTEXT.value).strip().lower()
    try:
        return SearchMode(normalized_value)
    except ValueError as exc:
        raise ConfigurationError(f"{config_name} 仅支持 fulltext 或 fuzzy。") from exc


def _parse_postgresql_default_search_mode() -> SearchMode:
    """解析 PostgreSQL 默认搜索模式，兼容旧环境变量。"""

    explicit_mode = os.getenv("POSTGRESQL_DEFAULT_SEARCH_MODE")
    if explicit_mode is not None and explicit_mode.strip() != "":
        return _parse_search_mode(
            explicit_mode,
            config_name="POSTGRESQL_DEFAULT_SEARCH_MODE",
        )

    legacy_backend = os.getenv("POSTGRESQL_SEARCH_BACKEND")
    if legacy_backend is None or legacy_backend.strip() == "":
        return SearchMode.FULLTEXT

    normalized_legacy_backend = legacy_backend.strip().lower()
    if normalized_legacy_backend == "pgroonga":
        return SearchMode.FULLTEXT
    if normalized_legacy_backend == "pg_trgm":
        return SearchMode.FUZZY
    raise ConfigurationError(
        "POSTGRESQL_SEARCH_BACKEND 仅支持 pgroonga 或 pg_trgm；"
        "建议改用 POSTGRESQL_DEFAULT_SEARCH_MODE。"
    )


def _parse_search_dirs(raw_value: str | None, project_root: Path) -> tuple[Path, ...]:
    """解析默认索引目录列表。"""

    if raw_value is None or raw_value.strip() == "":
        return tuple()

    directories: list[Path] = []
    for item in raw_value.split(","):
        stripped = item.strip()
        if stripped == "":
            continue
        path = Path(stripped)
        if not path.is_absolute():
            path = (project_root / path).resolve()
        directories.append(path)
    return tuple(directories)


def _parse_string_tuple(
    raw_value: str | None,
    *,
    default: tuple[str, ...],
) -> tuple[str, ...]:
    """把逗号分隔字符串解析为去重后的元组。

    - 环境变量缺失时使用默认值。
    - 显式传空字符串时返回空元组，表示关闭默认规则。
    """

    if raw_value is None:
        return default
    if raw_value.strip() == "":
        return tuple()

    items: list[str] = []
    seen: set[str] = set()
    for item in raw_value.split(","):
        normalized = item.strip()
        if normalized == "":
            continue
        lowered = normalized.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        items.append(normalized)
    return tuple(items)


def load_app_config(force_reload: bool = False) -> AppConfig:
    """从 .env 和环境变量加载配置，并做基础归一化。"""

    global _CONFIG_CACHE

    if _CONFIG_CACHE is not None and not force_reload:
        return _CONFIG_CACHE

    project_root = Path(__file__).resolve().parents[2]
    load_dotenv(project_root / ".env", override=False)

    raw_backend = os.getenv("DATABASE_BACKEND", DatabaseBackend.SQLITE.value)
    try:
        database_backend = DatabaseBackend(raw_backend)
    except ValueError as exc:
        raise ConfigurationError(f"不支持的数据库后端：{raw_backend}") from exc

    sqlite_db_path = Path(os.getenv("SQLITE_DB_PATH", "data/search.db"))
    if not sqlite_db_path.is_absolute():
        sqlite_db_path = (project_root / sqlite_db_path).resolve()

    large_file_threshold_mb = _parse_positive_int(
        os.getenv("LARGE_FILE_THRESHOLD_MB"),
        15,
        "LARGE_FILE_THRESHOLD_MB",
    )
    very_large_file_threshold_mb = _parse_positive_int(
        os.getenv("VERY_LARGE_FILE_THRESHOLD_MB"),
        50,
        "VERY_LARGE_FILE_THRESHOLD_MB",
    )
    if very_large_file_threshold_mb < large_file_threshold_mb:
        raise ConfigurationError("VERY_LARGE_FILE_THRESHOLD_MB 不能小于 LARGE_FILE_THRESHOLD_MB。")

    _CONFIG_CACHE = AppConfig(
        database_backend=database_backend,
        postgresql_default_search_mode=_parse_postgresql_default_search_mode(),
        sqlite_db_path=sqlite_db_path,
        database_url=os.getenv("DATABASE_URL"),
        search_dirs=_parse_search_dirs(os.getenv("SEARCH_DIRS"), project_root),
        excluded_dir_keywords=_parse_string_tuple(
            os.getenv("EXCLUDED_DIR_KEYWORDS"),
            default=(".assets",),
        ),
        flask_host=os.getenv("FLASK_HOST", "127.0.0.1"),
        flask_port=int(os.getenv("FLASK_PORT", "5000")),
        flask_debug=_parse_bool(os.getenv("FLASK_DEBUG"), False),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        markitdown_timeout_seconds=_parse_positive_int(
            os.getenv("MARKITDOWN_TIMEOUT_SECONDS"),
            90,
            "MARKITDOWN_TIMEOUT_SECONDS",
        ),
        large_file_threshold_mb=large_file_threshold_mb,
        very_large_file_threshold_mb=very_large_file_threshold_mb,
        large_file_index_mode=_parse_large_file_index_mode(os.getenv("LARGE_FILE_INDEX_MODE")),
        project_root=project_root,
    )
    return _CONFIG_CACHE


def ensure_runtime_directories(config: AppConfig) -> None:
    """确保数据库、日志和数据目录存在。"""

    if config.database_backend is DatabaseBackend.SQLITE:
        config.sqlite_db_path.parent.mkdir(parents=True, exist_ok=True)
    (config.project_root / "logs").mkdir(parents=True, exist_ok=True)
    (config.project_root / "data").mkdir(parents=True, exist_ok=True)

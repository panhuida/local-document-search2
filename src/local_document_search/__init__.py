"""应用工厂、运行时引导与服务容器定义。"""

from __future__ import annotations

from dataclasses import dataclass

from flask import Flask

from local_document_search.config import AppConfig, ensure_runtime_directories, load_app_config
from local_document_search.converters import ConverterFactory
from local_document_search.persistence.database import (
    configure_database,
    get_session_factory,
    initialize_database,
)
from local_document_search.services import (
    CleanService,
    DocumentService,
    ErrorRecordService,
    FileOpenerService,
    IndexService,
    IndexTaskService,
    RetryTaskService,
    SearchService,
)
from local_document_search.utils import format_datetime_for_local_display
from local_document_search.utils.logging_config import setup_logging


@dataclass(frozen=True)
class ServiceContainer:
    """集中保存 Web 与 CLI 共享的 Service 实例。"""

    config: AppConfig
    index_service: IndexService
    index_task_service: IndexTaskService
    search_service: SearchService
    error_record_service: ErrorRecordService
    retry_task_service: RetryTaskService
    clean_service: CleanService
    document_service: DocumentService
    file_opener_service: FileOpenerService


def bootstrap_runtime(force_reload: bool = False) -> AppConfig:
    """完成配置、日志、数据库连接与轻量初始化。"""

    config = load_app_config(force_reload=force_reload)
    ensure_runtime_directories(config)
    setup_logging(config)
    configure_database(config)
    # 常规启动只确保表结构与搜索对象存在，不做全量 FTS 重建。
    initialize_database(rebuild_index=False)
    return config


def _create_service_container(config: AppConfig) -> ServiceContainer:
    """基于当前运行时配置构建 Service 容器。"""
    session_factory = get_session_factory()
    converter_factory = ConverterFactory(
        markitdown_timeout_seconds=config.markitdown_timeout_seconds,
        large_file_threshold_mb=config.large_file_threshold_mb,
        very_large_file_threshold_mb=config.very_large_file_threshold_mb,
        large_file_index_mode=config.large_file_index_mode,
    )
    index_service = IndexService(
        session_factory,
        default_search_dirs=config.search_dirs,
        excluded_dir_keywords=config.excluded_dir_keywords,
        converter_factory=converter_factory,
    )
    error_record_service = ErrorRecordService(
        session_factory,
        converter_factory=converter_factory,
    )
    return ServiceContainer(
        config=config,
        index_service=index_service,
        index_task_service=IndexTaskService(index_service),
        search_service=SearchService(
            session_factory,
            config.database_backend,
            config.postgresql_search_backend,
        ),
        error_record_service=error_record_service,
        retry_task_service=RetryTaskService(error_record_service),
        clean_service=CleanService(session_factory),
        document_service=DocumentService(session_factory),
        file_opener_service=FileOpenerService(session_factory),
    )


def build_service_container(
    force_reload: bool = False,
    config: AppConfig | None = None,
) -> ServiceContainer:
    """按当前配置构建一组可复用的业务服务。"""

    current_config = config if config is not None else bootstrap_runtime(force_reload=force_reload)
    if config is not None:
        ensure_runtime_directories(current_config)
        setup_logging(current_config)
        configure_database(current_config)
        initialize_database(rebuild_index=False)
    return _create_service_container(current_config)


def create_app(
    config: AppConfig | None = None,
    services: ServiceContainer | None = None,
) -> Flask:
    """创建 Flask 应用并注册蓝图。"""

    if services is not None:
        current_config = services.config
        ensure_runtime_directories(current_config)
        setup_logging(current_config)
        configure_database(current_config)
        initialize_database(rebuild_index=False)
        current_services = services
    else:
        current_config = config if config is not None else bootstrap_runtime()
        if config is not None:
            ensure_runtime_directories(current_config)
            setup_logging(current_config)
            configure_database(current_config)
            initialize_database(rebuild_index=False)
        current_services = _create_service_container(current_config)

    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["FLASK_HOST"] = current_config.flask_host
    app.config["FLASK_PORT"] = current_config.flask_port
    app.config["FLASK_DEBUG"] = current_config.flask_debug
    app.extensions["services"] = current_services
    app.add_template_filter(format_datetime_for_local_display, "local_datetime")

    from local_document_search.routes import web_blueprint

    app.register_blueprint(web_blueprint)
    return app

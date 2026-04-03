"""日志初始化工具。"""

from __future__ import annotations

import logging
from datetime import date

from local_document_search.config import AppConfig

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s:%(lineno)d %(message)s"
_LOGGING_CONFIGURED = False


def setup_logging(config: AppConfig) -> None:
    """按项目约定初始化文件与控制台日志。"""
    global _LOGGING_CONFIGURED
    root_logger = logging.getLogger()
    if _LOGGING_CONFIGURED:
        return

    root_logger.setLevel(config.log_level)

    formatter = logging.Formatter(LOG_FORMAT)
    log_file = config.project_root / "logs" / f"app-{date.today().isoformat()}.log"

    # 文件日志用于留痕，控制台日志用于开发期即时反馈。
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(config.log_level)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(config.log_level)

    root_logger.handlers.clear()
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    _LOGGING_CONFIGURED = True

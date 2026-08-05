# ponytail: 结构化日志(structlog) — 替代散落print; 开发期ConsoleRenderer彩色, 生产换JSONRenderer
import logging

import structlog


def setup_logging(level: int = logging.INFO) -> None:
    """配置 structlog(全局)。在 app 启动最早处调用(main 顶部)。"""
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=True,
    )


# 模块级 logger; 各处 `from src.core.logging import log` 取用
log = structlog.get_logger()

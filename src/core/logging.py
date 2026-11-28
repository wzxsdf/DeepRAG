# ponytail: 结构化日志(structlog) — 替代散落print; 开发期ConsoleRenderer彩色, 生产换JSONRenderer
# 架构优化④(20261129): request_id经contextvars贯穿 — 并发请求的节点日志可串联(此前交织无法区分一次问答的全链路)
import logging
import uuid
import contextvars

import structlog

# 请求级上下文: API中间件注入, 评测/脚本可手动bind(不设则日志无request_id字段, 向后兼容)
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")


def new_request_id() -> str:
    """生成并绑定新request_id(中间件每请求调用一次); 返回值供响应头X-Request-Id回传."""
    rid = uuid.uuid4().hex[:12]
    request_id_var.set(rid)
    return rid


def _inject_request_id(logger, method_name, event_dict):
    rid = request_id_var.get("")
    if rid:
        event_dict["request_id"] = rid
    return event_dict


def setup_logging(level: int = logging.INFO) -> None:
    """配置 structlog(全局)。在 app 启动最早处调用(main 顶部)。"""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _inject_request_id,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=True,
    )


# 模块级 logger; 各处 `from src.core.logging import log` 取用
log = structlog.get_logger()

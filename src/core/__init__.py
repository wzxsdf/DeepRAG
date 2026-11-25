# ponytail: infra 基础设施层(config/llm/lifespan/container/logging)
# PEP 562 惰性导出: 包init不急切导入llm栈(langchain/langgraph重量级依赖)。
# 根因: 旧的急切 `from src.core.llm import ...` 使 import src.core.logging(仅需structlog)
# 也级联拉起整个LLM依赖, CI最小依赖闭包在测试收集阶段ModuleNotFoundError(20261125实测踩坑)。
# 仓库内无 `from src.core import X` 消费方(均直接from子模块导入), 此处仅为API兼容保留。
from typing import Any
from importlib import import_module

_EXPORTS = {
    "get_settings": ("src.core.config", "get_settings"),
    "Settings": ("src.core.config", "Settings"),
    "ModelGateway": ("src.core.llm", "ModelGateway"),
    "get_gateway": ("src.core.llm", "get_gateway"),
    "chat": ("src.core.llm", "chat"),
    "chat_json": ("src.core.llm", "chat_json"),
    "chat_stream": ("src.core.llm", "chat_stream"),
    "log": ("src.core.logging", "log"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    if name in _EXPORTS:
        module_path, attr = _EXPORTS[name]
        return getattr(import_module(module_path), attr)
    raise AttributeError(f"module 'src.core' has no attribute '{name}'")

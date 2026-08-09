# src/core/llm: LLM模块 - 统一的LLM接口层

from src.core.llm.gateway import ModelGateway, get_gateway
from src.core.llm.chat import chat, chat_json, chat_stream

__all__ = ["ModelGateway", "get_gateway", "chat", "chat_json", "chat_stream"]

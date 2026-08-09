# ponytail: 向后兼容 - core/llm.py 重定向到 core/llm/

# 向后兼容：core/llm.py 重定向到 core/llm/
from src.core.llm import ModelGateway, get_gateway
from src.core.llm import chat, chat_json, chat_stream

__all__ = ["ModelGateway", "get_gateway", "chat", "chat_json", "chat_stream"]

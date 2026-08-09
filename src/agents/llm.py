# ponytail: LLM封装层 - 向后兼容，重定向到core/llm

# 向后兼容：agents/llm.py 重定向到 core/llm
from src.core.llm import chat, chat_json, chat_stream

__all__ = ["chat", "chat_json", "chat_stream"]

# ponytail: LLM调用port(架构优化②, 20261129) — graph节点经注入实例调用, 替代模块级硬编码
# 默认实现懒委托src.core.llm模块函数(行为零变化, 测试可继续在模块缝上打桩);
# 注入自定义子类即可替换LLM实现(测试注入fake/未来按租户换模型), 无需monkeypatch全局。


from typing import AsyncIterator, Optional


class LLMClient:
    """LLM调用port: chat/chat_json/chat_stream 三方法(与src.core.llm模块函数同签名).

    - 默认实例(get_default_llm_client单例)懒委托模块函数 — 不预建连接, 不改变任何现有行为
    - 子类覆写即可注入: build_graph(..., llm=FakeLLMClient())
    """

    async def chat(self, messages: list[dict], temperature: float = 0.0) -> str:
        from src.core.llm import chat as _chat  # 调用时解析: 测试monkeypatch src.core.llm.chat 可拦截

        return await _chat(messages, temperature)

    async def chat_json(self, messages: list[dict], temperature: float = 0.0,
                        schema: Optional[dict] = None, purpose: str = "fast") -> dict:
        from src.core.llm import chat_json as _chat_json

        return await _chat_json(messages, temperature, schema, purpose)

    async def chat_stream(self, messages: list[dict]) -> AsyncIterator[str]:
        from src.core.llm import chat_stream as _chat_stream

        async for tok in _chat_stream(messages):
            yield tok


_client = None


def get_default_llm_client() -> LLMClient:
    """进程级默认单例(None则懒建). 注入面: build_graph(llm=...) 传自定义实例."""
    global _client
    if _client is None:
        _client = LLMClient()
    return _client

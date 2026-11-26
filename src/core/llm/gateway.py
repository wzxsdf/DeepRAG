# ponytail: ModelGateway — 统一LLM入口, init_chat_model 解析 "provider:model" → BaseChatModel
# 消除 agents/llm.py 的httpx手写 + tools.py 的ChatOpenAI 两套调用; langfuse自动trace(callbacks bind)
# purpose路由: generate(生成强)/fast(判断快省)/reason(规划); 多厂商靠provider前缀, 一家多模型靠不同purpose

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from src.core.config import get_settings, Settings
from src.core.logging import log


class ModelGateway:
    """统一模型入口: purpose→BaseChatModel(按purpose缓存).
    CRAG(chat/chat_json/chat_stream)与create_agent都从此取model, 消除httpx手写+ChatOpenAI两套调用."""

    def __init__(self, settings: Settings):
        self._s = settings
        self._cache: dict[str, BaseChatModel] = {}
        self._handler = self._make_handler()

    def _make_handler(self):
        """langfuse LangChain callback(三key齐全才建); 失败不阻塞, 降级无trace."""
        if not self._s.langfuse_enabled:
            return None
        try:
            from langfuse.langchain import CallbackHandler
            return CallbackHandler()
        except Exception as e:
            log.warning("langfuse handler 建立失败, 降级无trace", error=type(e).__name__, msg=str(e))
            return None

    def _purpose_model(self, purpose: str) -> str:
        """purpose→'provider:model'字符串; 字段空→回退 openai:{litellm_model} 兼容现有单端点."""
        s = self._s
        field = {"generate": s.chat_generate_model,
                 "fast": s.chat_fast_model,
                 "reason": s.chat_reason_model,
                 "judge": s.chat_judge_model}.get(purpose, s.chat_fast_model)
        return field or f"openai:{s.litellm_model}"

    def chat_model(self, purpose: str = "fast") -> BaseChatModel:
        """取(并缓存)purpose对应的BaseChatModel. provider前缀→providers取连接信息→init_chat_model."""
        if purpose not in self._cache:
            model_str = self._purpose_model(purpose)
            provider = model_str.split(":", 1)[0]
            pcfg = self._s.providers.get(provider)
            kwargs: dict = {"temperature": 0, "max_retries": 2}
            # ponytail: purpose级输出token封顶(config默认0=不封顶); 长输出只在长尾出现, 封顶直接压p95
            purpose_max_tokens = {"generate": self._s.chat_generate_max_tokens,
                                  "fast": self._s.chat_fast_max_tokens}.get(purpose, 0)
            if purpose_max_tokens > 0:
                kwargs["max_tokens"] = purpose_max_tokens
            if pcfg:
                if pcfg.api_key:
                    kwargs["api_key"] = pcfg.api_key
                if pcfg.base_url:
                    kwargs["base_url"] = pcfg.base_url
            if self._handler:
                kwargs["callbacks"] = [self._handler]  # ponytail: bind langfuse trace 到该model所有调用
            self._cache[purpose] = init_chat_model(model_str, **kwargs)
            log.info("model_gateway init", purpose=purpose, model=model_str, langfuse=bool(self._handler))
        return self._cache[purpose]


_gateway: ModelGateway | None = None


def get_gateway() -> ModelGateway:
    """全局 ModelGateway 单例(懒加载). agents/llm.py + tools.get_react_agent 经此取model, 与settings单例同模式."""
    global _gateway
    if _gateway is None:
        _gateway = ModelGateway(get_settings())
    return _gateway


if __name__ == "__main__":  # ponytail: 自检 — 三purpose回退litellm_model + 缓存 + langfuse开关
    gw = get_gateway()
    for p in ("generate", "fast", "reason"):
        m = gw.chat_model(p)
        print(f"{p}: {type(m).__name__} (cache hit: {gw.chat_model(p) is m})")
    print(f"langfuse handler active: {gw._handler is not None}")

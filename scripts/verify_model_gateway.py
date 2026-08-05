# ponytail: 阶段2迁移前置go/no-go — 验证init_chat_model(base_url覆盖juaiapi)+json+stream+langfuse handler
import os, asyncio
from dotenv import load_dotenv
load_dotenv()

# 1) langfuse langchain handler(已配置key, 验证可建)
HANDLER = None
try:
    from langfuse.langchain import CallbackHandler as LangfuseCallbackHandler
    HANDLER = LangfuseCallbackHandler()
    print(f"[1] langfuse CallbackHandler OK: {type(HANDLER).__name__}")
except Exception as e:
    print(f"[1] langfuse handler FAIL: {e!r}")

# 2) init_chat_model + base_url覆盖 + ainvoke/json/stream
from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage

MODEL = "openai:" + os.getenv("LITELLM_MODEL", "gpt-4o-mini")
m = init_chat_model(MODEL, base_url=os.getenv("LITELLM_BASE_URL"),
                    api_key=os.getenv("LITELLM_API_KEY"), temperature=0, max_retries=2)
print(f"[2] init_chat_model OK: {MODEL} -> {type(m).__name__}")


async def test():
    cbs = [HANDLER] if HANDLER else None
    # 普通chat(带langfuse callback)
    r = await m.ainvoke([HumanMessage(content="只回复两个字: 可以")], config=({"callbacks": cbs} if cbs else {}))
    print(f"[3] ainvoke: {r.content[:40]}")
    # json mode(response_format bind)
    mj = m.bind(response_format={"type": "json_object"})
    rj = await mj.ainvoke([HumanMessage(content='严格返回JSON: {"ok": true, "n": 1}')])
    print(f"[4] json bind: {rj.content[:80]}")
    # stream
    toks = []
    async for chunk in m.astream([HumanMessage(content="数1到3, 用逗号分隔")]):
        if chunk.content:
            toks.append(chunk.content)
    print(f"[5] stream tokens: {''.join(toks)[:40]}")
    print("\n=== ModelGateway 迁移可行性: OK ✅ ===" if r.content else "FAIL")


asyncio.run(test())

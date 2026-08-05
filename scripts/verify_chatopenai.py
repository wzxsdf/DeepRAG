# ponytail: 迁移前置go/no-go — 验证ChatOpenAI能连juaiapi并支持工具调用(决定能否用create_agent)
import os, asyncio
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool

load_dotenv()
raw = os.getenv("LITELLM_BASE_URL", "")
api_key = os.getenv("LITELLM_API_KEY", "")
model = os.getenv("LITELLM_MODEL", "gpt-4o-mini")
print(f"BASE_URL raw: {raw!r}  model: {model}")


@tool
def get_weather(city: str) -> str:
    """查询指定城市的天气。"""
    return f"{city} 晴 25°C"


async def try_base(base_url: str, label: str) -> bool:
    try:
        llm = ChatOpenAI(model=model, base_url=base_url, api_key=api_key, temperature=0)
        r = await llm.ainvoke("只回复两个字: 你好")
        print(f"[{label}] basic OK: {r.content[:30]!r}")
        llm_t = llm.bind_tools([get_weather])
        r2 = await llm_t.ainvoke("北京今天天气怎么样?")
        tc = r2.tool_calls
        print(f"[{label}] tool_calls: {tc}")
        return bool(tc)
    except Exception as e:
        print(f"[{label}] FAIL: {type(e).__name__}: {str(e)[:180]}")
        return False


async def main():
    ok = False
    for b in [raw, raw.rstrip("/") + "/v1"]:
        if await try_base(b, b):
            ok = True
            break
    print(f"\n=== ChatOpenAI + juaiapi + 工具调用: {'OK ✅ 迁移可行' if ok else 'FAIL ❌ 需调整LLM适配'} ===")


if __name__ == "__main__":
    asyncio.run(main())

import asyncio
from src.agents.tools import run_tool_agent

async def main():
    res = await run_tool_agent("北京今天天气怎么样")
    print("source:", res["source"])
    print("answer:", res["generation"][:200])
    assert res["source"] in ("tool", "fallback_unverified"), f"异常 source: {res['source']}"
    assert res["generation"], "空回答"
    print("run_tool_agent ok")

asyncio.run(main())

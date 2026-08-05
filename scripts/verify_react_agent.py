# ponytail: 迁移核心验证 — create_agent 构建 + run_react_agent 端到端(不经后端, 直测)
import asyncio
from src.retrieval.hybrid import HybridRetriever
from src.agents.tools import run_react_agent


async def main():
    retriever = HybridRetriever()
    # ① 简单工具调用(天气) — 验证 create_agent 构建+astream+工具执行+source判定
    res = await run_react_agent(retriever, "北京今天天气怎么样?", tenant_id="default", kb_ids=None)
    print(f"[天气] source={res['source']} gen={res['generation'][:100]!r}")
    assert res["source"] == "tool", f"天气应调工具, 实际 source={res['source']}"
    assert "北京" in res["generation"] or "°" in res["generation"] or "晴" in res["generation"], "天气答案异常"
    print("PASS: create_agent 工具调用 + streaming 映射 OK")


if __name__ == "__main__":
    asyncio.run(main())

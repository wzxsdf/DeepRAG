# ponytail: 直接验证 run_plan_agent 功能(绕过 _judge_complex 路由不确定性, 隔离测试多源编排)
# 路由层(agent模式简单/多源分发)的端到端验证在 test_modes.py; 此处专注 plan_agent 自身
import asyncio
from src.retrieval.hybrid import HybridRetriever
from src.agents.tools import run_plan_agent


async def main():
    retriever = HybridRetriever()
    # 多源问题: KB事实 + 计算 — 应规划多步(kb+calc)并综合, source=planned
    q = "简历里这个人掌握哪些技术栈？顺便帮我算一下 365 乘以 3 除以 7 等于多少"
    res = await run_plan_agent(retriever, q, tenant_id="default", kb_ids=None)
    ok = res["source"] == "planned" and bool(res["generation"])
    print(f"[{'PASS' if ok else 'FAIL'}] plan_agent·多源: source={res['source']} ans={res['generation'][:80]!r}")
    assert ok, f"plan_agent多源 fail: {res['source']}"

    # 单源(KB)问题 — plan自适应1步, 仍 source=planned, 验证不崩
    res2 = await run_plan_agent(retriever, "简历里掌握哪些技术栈", tenant_id="default", kb_ids=None)
    ok2 = res2["source"] == "planned" and bool(res2["generation"])
    print(f"[{'PASS' if ok2 else 'FAIL'}] plan_agent·单源: source={res2['source']} (plan自适应1步)")
    assert ok2, f"plan_agent单源 fail: {res2['source']}"
    print("test_plan_agent all pass")


if __name__ == "__main__":
    asyncio.run(main())

#!/usr/bin/env python
# 验证 tool_agent / plan_agent 的长期记忆注入
# 前置: Qdrant + MySQL + LLM 已配置
# 运行: python scripts/test_tool_plan_inject.py
#
# 策略: 写入强标记记忆(pet_name=旺财), 问"我的宠物叫什么", 验证 LLM 从 memory_context 读出

import asyncio, sys
from src.core.config import get_settings
from src.storage.mysql import MySQLStore
from src.memory import LongTermMemoryStore, UserFactStore

TEST_USER = "tool-plan-inject-test"


async def main():
    s = get_settings()
    mysql = MySQLStore(cfg=s.mysql_cfg); await mysql.connect()
    store = LongTermMemoryStore(qdrant_cfg=s.qdrant_cfg, decay_lambda=s.memory_decay_lambda)
    fact = UserFactStore(mysql); await fact.ensure_schema()

    # 写入强标记记忆
    await store.delete_by_user(TEST_USER); await fact.delete_by_user(TEST_USER)
    await fact.upsert(TEST_USER, "pet_name", "旺财")
    await store.add(TEST_USER, "用户的宠物狗名叫旺财", memory_type="semantic")
    print("[setup] 写入记忆: pet_name=旺财")

    # 1. _entry_node 检索(验证能拿到, 含实体+长期)
    from src.agents.graph import _make_entry_node
    entry = _make_entry_node(store, fact)
    cmd = await entry({"question": "我的宠物叫什么名字", "mode": "quick", "user_id": TEST_USER})
    mem_ctx = cmd.update.get("memory_context", "")
    print(f"[1] _entry_node memory_context:\n{mem_ctx}")
    assert "旺财" in mem_ctx, "记忆未被检索"

    # 2. tool_agent (run_react_agent) 注入验证
    print("\n[2] tool_agent (run_react_agent) 注入验证...")
    from src.agents.react_agent import run_react_agent
    class MockRet:
        async def retrieve(self, q, f, top_k): return []
    res = await run_react_agent(MockRet(), "我的宠物叫什么名字？",
                                history=None, memory_context=mem_ctx)
    ans_tool = res["generation"]
    print(f"    回答: {ans_tool[:80]}")
    print(f"    source={res['source']}")
    assert "旺财" in ans_tool, f"tool_agent 未从记忆读出! 回答: {ans_tool}"
    print("    [OK] tool_agent 记忆注入生效(回答含'旺财')")

    # 3. plan_agent (run_plan_agent) 注入验证
    print("\n[3] plan_agent (run_plan_agent) 注入验证...")
    from src.agents.plan_agent import run_plan_agent
    res2 = await run_plan_agent(MockRet(), "我的宠物叫什么名字？",
                                history=None, memory_context=mem_ctx)
    ans_plan = res2["generation"]
    print(f"    回答: {ans_plan[:80]}")
    assert "旺财" in ans_plan, f"plan_agent 未从记忆读出! 回答: {ans_plan}"
    print("    [OK] plan_agent 记忆注入生效(回答含'旺财')")

    await store.delete_by_user(TEST_USER); await fact.delete_by_user(TEST_USER)
    await mysql.close()
    print("\nSUCCESS: tool_agent + plan_agent 长期记忆注入均生效")


if __name__ == "__main__":
    asyncio.run(main())

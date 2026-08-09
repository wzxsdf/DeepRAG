#!/usr/bin/env python
# P1 长期记忆读闭环端到端验证: 写入 → 语义检索 → graph注入
#
# 前置: Qdrant 运行 + embedding API(SiliconFlow BGE-M3) 已配置 .env
# 运行: python scripts/test_memory_p1.py
#
# 验证三件事:
#   1. LongTermMemoryStore 写入 agent_memory collection + 按 user_id 隔离检索
#   2. 语义检索命中(不同表述, 同义召回)
#   3. graph _entry_node 读闭环: 检索结果注入 state.memory_context

import asyncio
import sys

from src.memory import LongTermMemoryStore
from src.core.config import get_settings


TEST_USER = "p1-verify-user"


async def part1_store_crud(store: LongTermMemoryStore):
    """验证 写入 / 语义检索 / 用户隔离。"""
    print("\n=== Part1: LongTermMemoryStore 写/检索 ===")
    await store.delete_by_user(TEST_USER)  # 清理旧数据

    # 写入三类记忆
    await store.add(TEST_USER, "用户是Python后端开发者, 偏好FastAPI框架", memory_type="semantic")
    await store.add(TEST_USER, "上周协助用户优化了RAG系统的混合检索", memory_type="episodic")
    await store.add(TEST_USER, "用户喜欢简洁的代码风格, 反对冗余注释", memory_type="semantic")
    print("[write] 写入3条记忆(semantic x2, episodic x1)")

    # 语义检索: 用不同表述验证同义召回
    cases = [
        ("帮我写个接口", "FastAPI"),
        ("之前检索那个项目", "混合检索"),
        ("代码风格有什么讲究", "简洁"),
    ]
    for query, expect_keyword in cases:
        results = await store.search(TEST_USER, query, top_k=2)
        hit = any(expect_keyword in r for r in results)
        status = "OK" if hit else "MISS"
        print(f"[search] {status} query='{query}' 期望含'{expect_keyword}' -> {[r[:30] for r in results]}")
        assert hit, f"语义检索未命中期望关键词: {expect_keyword}"

    # 用户隔离: 另一用户检索不应命中
    other = await store.search("other-user", "FastAPI", top_k=2)
    print(f"[isolation] other-user 检索结果: {other} (应为空)")
    assert other == [], "用户隔离失效"

    await store.delete_by_user(TEST_USER)
    print("[cleanup] 测试记忆已清理")


async def part2_read_loop(store: LongTermMemoryStore):
    """验证 graph _entry_node 读闭环: 检索 → 注入 memory_context(不跑全图LLM)。"""
    print("\n=== Part2: graph 读闭环(注入memory_context) ===")
    from src.agents.graph import _make_entry_node

    await store.delete_by_user(TEST_USER)
    await store.add(TEST_USER, "用户偏好TypeScript, 项目用React", memory_type="semantic")

    entry = _make_entry_node(store)
    # 直接调用节点(走kb分支, 避免触发LLM意图判断)
    cmd = await entry({
        "question": "前端怎么写组件",
        "mode": "kb",
        "user_id": TEST_USER,
    })
    mem_ctx = cmd.update.get("memory_context", "")
    print(f"[inject] memory_context: {mem_ctx[:80]}...")
    assert "TypeScript" in mem_ctx or "React" in mem_ctx, "记忆未注入"
    assert cmd.update.get("user_id") == TEST_USER
    print(f"[inject] OK: 命中记忆已注入, goto={cmd.goto}")

    await store.delete_by_user(TEST_USER)


async def main():
    s = get_settings()
    print(f"Qdrant: {s.qdrant_cfg.host}:{s.qdrant_cfg.port}")
    print(f"长期记忆开关: memory_long_term_enabled={s.memory_long_term_enabled}")

    try:
        store = LongTermMemoryStore(qdrant_cfg=s.qdrant_cfg)
    except Exception as e:
        print(f"\n[ERROR] 无法连接Qdrant: {e}", file=sys.stderr)
        print("请先启动: docker compose up -d qdrant", file=sys.stderr)
        sys.exit(1)

    await part1_store_crud(store)
    await part2_read_loop(store)
    print("\nSUCCESS: P1 长期记忆读闭环端到端验证通过")


if __name__ == "__main__":
    asyncio.run(main())

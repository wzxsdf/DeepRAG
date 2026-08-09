#!/usr/bin/env python
# P2 写闭环端到端验证: 一轮对话 → 自动抽取沉淀 → 下次读注入
#
# 前置: MySQL + Qdrant 运行 + LLM/embedding API 已配置 .env
# 运行: python scripts/test_memory_p2.py
#
# 验证三件事(真实LLM抽取):
#   1. MemoryWriter 从对话抽取事实 → user_facts 表
#   2. MemoryWriter 沉淀长期记忆 → agent_memory
#   3. 下一轮 _entry_node 读到实体记忆 + 长期记忆, 注入 memory_context

import asyncio
import sys

from src.core.config import get_settings
from src.storage.mysql import MySQLStore
from src.memory import UserFactStore, LongTermMemoryStore, MemoryWriter


TEST_USER = "p2-verify-user"


async def main():
    s = get_settings()
    print(f"MySQL: {s.mysql_cfg.host}:{s.mysql_cfg.port}/{s.mysql_cfg.db}")
    print(f"写闭环开关: memory_auto_write={s.memory_auto_write}")

    # 1. 装配组件(复用lifespan同款)
    try:
        mysql = MySQLStore(cfg=s.mysql_cfg)
        await mysql.connect()
    except Exception as e:
        print(f"\n[ERROR] MySQL连接失败: {e}", file=sys.stderr)
        sys.exit(1)
    try:
        long_term = LongTermMemoryStore(qdrant_cfg=s.qdrant_cfg)
    except Exception as e:
        print(f"\n[ERROR] Qdrant连接失败: {e}", file=sys.stderr); sys.exit(1)

    fact_store = UserFactStore(mysql)
    await fact_store.ensure_schema()
    await fact_store.delete_by_user(TEST_USER)
    await long_term.delete_by_user(TEST_USER)
    writer = MemoryWriter(fact_store=fact_store, long_term_store=long_term)

    # 2. 模拟一轮有信息量的对话 → 写闭环
    print("\n=== Part1: 写闭环(真实LLM抽取) ===")
    question = "我是Python后端开发, 最近在做一个RAG系统, 用FastAPI做API层"
    answer = ("了解到您是Python后端开发者, 使用FastAPI。"
              "RAG系统通常包含文档解析、向量检索、生成三个核心模块。"
              "建议检索层用混合检索(dense+BM25)+重排序提升准确率。")
    stats = await writer.write(TEST_USER, question, answer, source="kb_grounded")
    print(f"[write] facts写入={stats['facts_written']}, 长期记忆={stats['long_term_written']}")

    # 3. 验证 user_facts 落表
    print("\n=== Part2: 验证沉淀结果 ===")
    facts = await fact_store.get_all(TEST_USER)
    print(f"[user_facts] {facts}")
    assert facts, "user_facts 未沉淀任何事实"
    # 至少命中一个预期关键词(Python/FastAPI/RAG)
    facts_blob = " ".join(facts.values()).lower()
    assert any(k in facts_blob for k in ["python", "fastapi", "rag"]), f"事实抽取未命中预期: {facts}"

    # 4. 验证 agent_memory 沉淀 + 语义检索
    hits = await long_term.search(TEST_USER, "用户的技术栈和项目", top_k=2)
    print(f"[agent_memory] 检索'技术栈和项目': {[h[:40] for h in hits]}")
    assert hits, "长期记忆未沉淀或检索失败"

    # 5. 验证下一轮读注入
    print("\n=== Part3: 下一轮 _entry_node 读注入 ===")
    from src.agents.graph import _make_entry_node
    entry = _make_entry_node(long_term, fact_store)
    cmd = await entry({"question": "帮我设计API", "mode": "kb", "user_id": TEST_USER})
    mem_ctx = cmd.update.get("memory_context", "")
    print(f"[memory_context]\n{mem_ctx}")
    assert "用户画像" in mem_ctx, "实体记忆未注入"
    assert "相关历史记忆" in mem_ctx, "长期记忆未注入"

    # 清理
    await fact_store.delete_by_user(TEST_USER)
    await long_term.delete_by_user(TEST_USER)
    await mysql.close()
    print("\nSUCCESS: P2 写闭环端到端验证通过 — 用得越多越懂你")


if __name__ == "__main__":
    asyncio.run(main())

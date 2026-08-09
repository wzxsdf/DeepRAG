#!/usr/bin/env python
# 健壮性/质量优化验证: A1持久化队列 + C1 key归一化 + G1敏感过滤 + F1指标
# 前置: MySQL + Qdrant 已配置
# 运行: python scripts/test_memory_robust.py

import asyncio, sys
from src.core.config import get_settings
from src.storage.mysql import MySQLStore
from src.memory import UserFactStore, MemoryWriteQueue, LongTermMemoryStore

TEST_USER = "robust-test"


async def main():
    s = get_settings()
    mysql = MySQLStore(cfg=s.mysql_cfg); await mysql.connect()
    fact = UserFactStore(mysql); await fact.ensure_schema()
    queue = MemoryWriteQueue(mysql); await queue.ensure_schema()
    try:
        long_term = LongTermMemoryStore(qdrant_cfg=s.qdrant_cfg)
    except Exception as e:
        print(f"Qdrant连接失败: {e}"); sys.exit(1)

    await fact.delete_by_user(TEST_USER)

    # === A1 持久化队列 ===
    print("=== A1 写队列持久化 ===")
    qid = await queue.enqueue(TEST_USER, "测试问题", "测试答案", "test_source")
    assert qid, "入队失败"
    pending = await queue.fetch_pending(10)
    assert any(i.id == qid and i.user_id == TEST_USER for i in pending), "fetch 未取到"
    print(f"[enqueue] OK: qid={qid}, fetch到{len(pending)}条pending")
    await queue.mark_done(qid)
    pending2 = await queue.fetch_pending(10)
    assert not any(i.id == qid for i in pending2), "mark_done 后仍被fetch"
    print(f"[consume] OK: mark_done后不再被消费")

    # === C1 key归一化(真实upsert) ===
    print("\n=== C1 key归一化 ===")
    await fact.upsert(TEST_USER, "Language_Pref", "Python")    # → preferred_language
    await fact.upsert(TEST_USER, "编程语言", "Go")              # 同义词覆盖 → preferred_language=Go
    await fact.upsert(TEST_USER, "当前项目", "DeepRAG")         # → current_project
    facts = await fact.get_all(TEST_USER)
    print(f"[facts] {facts}")
    assert "preferred_language" in facts and facts["preferred_language"] == "Go", "归一化/覆盖失败"
    assert "current_project" in facts, "中文同义词未归一化"
    assert "Language_Pref" not in facts and "编程语言" not in facts, "原始key残留"
    print("[normalize] OK: Language_Pref/编程语言→preferred_language, 当前项目→current_project")

    # === G1 敏感过滤 ===
    print("\n=== G1 敏感信息过滤 ===")
    before = len(facts)
    await fact.upsert(TEST_USER, "password", "mySecret123")     # key敏感词
    await fact.upsert(TEST_USER, "api_key", "abc")              # key敏感词
    long_token = "sk-abcdef1234567890ABCDEFabcdef1234567890AB"  # 41字符无空格高熵
    await fact.upsert(TEST_USER, "note", long_token)            # value像token
    facts2 = await fact.get_all(TEST_USER)
    print(f"[after敏感写入] {facts2}")
    assert "password" not in facts2 and "api_key" not in facts2, "key敏感词未拦截"
    assert "note" not in facts2, f"value启发式未拦截长token: {facts2.get('note')}"
    assert len(facts2) == before, "敏感数据被错误写入"
    print("[filter] OK: password/api_key(key词) + note长token(value启发式) 全部拦截")

    # === F1 指标 ===
    print("\n=== F1 记忆库指标 ===")
    lt_count = await long_term.count()
    fact_count = await fact.count()
    q_pending = await queue.count_pending()
    print(f"[metrics] agent_memory_total={lt_count}, user_facts_total={fact_count}, queue_pending={q_pending}")
    assert lt_count >= 0, "long_term count异常"
    assert fact_count >= 1, "user_facts count应含测试数据"

    # 清理
    await fact.delete_by_user(TEST_USER)
    await mysql.close()
    print("\nSUCCESS: A1/C1/G1/F1 全部验证通过")


if __name__ == "__main__":
    asyncio.run(main())

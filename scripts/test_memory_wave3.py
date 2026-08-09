#!/usr/bin/env python
# 第三波验证: F2编辑 / C2主题聚类 / B1写去重 / F3整合status
# 前置: Qdrant + embedding API
# 运行: python scripts/test_memory_wave3.py

import asyncio, sys
from unittest.mock import AsyncMock
from src.core.config import get_settings
from src.memory import LongTermMemoryStore, MemoryWriter, MemoryConsolidator

USER = "wave3-test"


async def main():
    s = get_settings()
    try:
        store = LongTermMemoryStore(qdrant_cfg=s.qdrant_cfg, decay_lambda=s.memory_decay_lambda)
    except Exception as e:
        print(f"Qdrant连接失败: {e}"); sys.exit(1)

    # === F2 编辑(update重embed) ===
    print("=== F2 记忆编辑(update) ===")
    await store.delete_by_user(USER)
    mid = await store.add(USER, "原始记忆内容Python", "episodic")
    ok = await store.update(mid, content="修改后的记忆TypeScript")
    assert ok, "update返回False"
    await asyncio.sleep(0.3)
    hits = await store.search(USER, "修改后TypeScript", top_k=1)
    assert "修改后" in " ".join(hits) and "TypeScript" in " ".join(hits), f"update未生效: {hits}"
    print(f"[F2] OK: update content后检索命中新内容({hits[0][:20]})")

    # importance 更新
    ok2 = await store.update(mid, importance=0.9)
    assert ok2
    print("[F2] OK: importance部分更新(不重embed)")

    # === C2 主题聚类 ===
    print("\n=== C2 抽象提炼按主题聚类 ===")
    consolidator = MemoryConsolidator(store)
    contents = [
        "用户写Python爬虫遇到反爬拦截",
        "用户抓电商数据被JS反爬挡住",
        "用户部署应用到阿里云OSS存储",
        "用户配置OSS桶的访问权限",
    ]
    clusters = await consolidator._cluster_by_topic(contents)
    print(f"[clusters] {len(clusters)}组: {clusters}")
    assert len(clusters) == 2, f"应分2主题组, 实际{len(clusters)}"
    # 验证组内不杂糅(爬虫和OSS不在同组)
    for c in clusters:
        blob = " ".join(c)
        mixed = ("爬虫" in blob or "反爬" in blob) and ("OSS" in blob or "存储" in blob)
        assert not mixed, f"组内主题杂糅: {c}"
    print("[C2] OK: 爬虫组/OSS组正确分离, 无杂糅")

    # === B1 写去重 ===
    print("\n=== B1 写闭环去重(近乎重复覆盖) ===")
    await store.delete_by_user(USER)
    writer = MemoryWriter(fact_store=None, long_term_store=store)

    async def mock_extract(q, a):
        return {"facts": {}, "memorable": True, "summary": "用户偏好Python后端开发用FastAPI"}
    writer._extract = mock_extract  # 避免 LLM, 固定相同summary

    await writer.write(USER, "问题1", "答案1", "test")
    count1 = await store.count(USER)
    await writer.write(USER, "问题2", "答案2", "test")  # 相同summary, 应覆盖
    count2 = await store.count(USER)
    print(f"[dedup] 第1次后={count1}条, 第2次后={count2}条")
    assert count2 == count1, f"B1去重失败: 重复写入未覆盖({count1}→{count2})"
    print("[B1] OK: 近乎重复summary覆盖(记忆数不增长)")

    # 不同summary应新增
    async def mock_extract2(q, a):
        return {"facts": {}, "memorable": True, "summary": "完全不同的项目部署经验"}
    writer._extract = mock_extract2
    await writer.write(USER, "q3", "a3", "test")
    count3 = await store.count(USER)
    assert count3 == count1 + 1, f"不同summary应新增: {count2}→{count3}"
    print(f"[B1] OK: 不同summary正常新增({count2}→{count3})")

    # === F3 整合status ===
    print("\n=== F3 整合status可观测 ===")
    consolidator2 = MemoryConsolidator(store)
    consolidator2._abstract_llm = AsyncMock(return_value="NONE")  # 避免 LLM
    await consolidator2.consolidate_all([USER])
    status = consolidator2.get_status()
    print(f"[status] {status}")
    assert status["run_count"] >= 1, "run_count未递增"
    assert status["last_run_at"], "last_run_at未记录"
    assert status["running"] is False, "整合后running应False"
    assert "total_deduped" in status["last_stats"], "last_stats缺失"
    print("[F3] OK: running/last_run_at/last_stats/run_count 全部正确")

    await store.delete_by_user(USER)
    print("\nSUCCESS: 第三波 F2/C2/B1/F3 全部验证通过")


if __name__ == "__main__":
    asyncio.run(main())

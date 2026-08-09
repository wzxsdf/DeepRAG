#!/usr/bin/env python
# 第二波"记忆活性管理"验证: 分类型衰减(E4) + LRU淘汰(E1) + 访问追踪(D2/D3) + 过期过滤(D4) + 截断(E3)
# 前置: Qdrant 运行
# 运行: python scripts/test_memory_liveness.py

import asyncio, sys
from datetime import datetime, timedelta
from src.core.config import get_settings
from src.memory import LongTermMemoryStore

TEST_USER = "liveness-test"


async def main():
    s = get_settings()
    # 用小 max_per_user 触发淘汰验证
    store = LongTermMemoryStore(qdrant_cfg=s.qdrant_cfg, decay_lambda=s.memory_decay_lambda,
                                max_per_user=3, time_decay=True)
    await store.delete_by_user(TEST_USER)

    # === D4 过期过滤 ===
    print("=== D4 valid_until 过期过滤 ===")
    expired = await store.add(TEST_USER, "已过期的记忆-不应被检索到", "episodic",
                              valid_until=datetime.now() - timedelta(days=1))
    fresh = await store.add(TEST_USER, "有效记忆-应被检索", "episodic")
    hits = await store.search(TEST_USER, "记忆", top_k=5)
    print(f"[search] 结果: {hits}")
    assert "有效记忆" in " ".join(hits), "有效记忆未命中"
    assert "已过期" not in " ".join(hits), "过期记忆未被过滤!"
    print("[D4] OK: 过期记忆被过滤")

    # === E4 分类型衰减 ===
    print("\n=== E4 分类型衰减(semantic不衰减) ===")
    await store.delete_by_user(TEST_USER)
    # 写一条老的 semantic + 一条老的 episodic (手动改 created_at 不可行, 用 importance 区分观察)
    # 用 search_with_scores 看 final_score: semantic 不受时间影响, episodic 衰减
    old_time = datetime.now() - timedelta(days=30)
    sem_id = await store.add(TEST_USER, "用户偏好Python(规律)", "semantic")
    epi_id = await store.add(TEST_USER, "上周帮用户调试bug(事件)", "episodic")
    # 手动把两条改成老时间(set_payload同步), 验证 semantic 不衰减
    store.client.set_payload(collection_name=store.collection_name,
        payload={"created_at": old_time.isoformat()}, points=[sem_id, epi_id])
    ranked = await store.search_with_scores(TEST_USER, "Python和bug", top_k=2)
    for r in ranked:
        print(f"  [{r['memory_type']}] score={r['score']:.3f} final={r['final_score']:.3f}")
    sem_r = next((r for r in ranked if r["memory_type"] == "semantic"), None)
    epi_r = next((r for r in ranked if r["memory_type"] == "episodic"), None)
    if sem_r and sem_r["created_at"] and sem_r["created_at"] < datetime.now() - timedelta(days=20):
        # semantic 老记忆: final 应≈score(不衰减)
        assert abs(sem_r["final_score"] - sem_r["score"]) < 0.01, f"semantic不应衰减: {sem_r}"
        print("[E4] OK: semantic 老记忆不衰减(final≈score)")
    if epi_r and epi_r["created_at"] and epi_r["created_at"] < datetime.now() - timedelta(days=20):
        # episodic 老记忆: final < score(衰减)
        assert epi_r["final_score"] < epi_r["score"], f"episodic应衰减: {epi_r}"
        print(f"[E4] OK: episodic 老记忆衰减(final<score)")

    # === D2/D3 访问追踪 ===
    print("\n=== D2/D3 access_count 访问追踪 ===")
    await asyncio.sleep(0.3)  # 等 _touch_accessed 异步完成
    items = await store.list_all(TEST_USER)
    sem_item = next((i for i in items if i["id"] == sem_id), None)
    print(f"  [semantic] access_count={sem_item['access_count'] if sem_item else '?'}")
    assert sem_item and sem_item["access_count"] >= 1, "检索命中后access_count未递增"
    print("[D2/D3] OK: 命中后 access_count 递增")

    # === E1 LRU 淘汰 ===
    print("\n=== E1 LRU淘汰(max_per_user=3) ===")
    await store.delete_by_user(TEST_USER)
    for i in range(5):  # 写5条, 上限3, 应淘汰2条最老/低命中的
        await store.add(TEST_USER, f"记忆条目{i}", "episodic")
    final = await store.list_all(TEST_USER)
    print(f"[evict] 写入5条上限3, 剩余{len(final)}条")
    assert len(final) == 3, f"LRU淘汰异常: 剩{len(final)}条, 期望3"
    print("[E1] OK: 超容量 LRU 淘汰到上限")

    await store.delete_by_user(TEST_USER)
    print("\nSUCCESS: 第二波 E1/E4/D2/D3/D4 全部验证通过")


if __name__ == "__main__":
    asyncio.run(main())

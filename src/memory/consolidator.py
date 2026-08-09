# 记忆整合器 — P3 核心
#
# 职责: 定期清理 + 升华长期记忆, 防越积越乱(设计文档 §8 三个环节)。
#   - 去重: 语义相近的记忆合并(相似度>阈值), 删冗余保留更全的
#   - 抽象提炼: 情节记忆 → 语义记忆(LLM总结规律), 信息密度提升
#   - 冲突消解: 融入抽象提炼(LLM以时间更新者为准)
# 解耦: 纯组件, 依赖 LongTermMemoryStore + LLM; 不依赖 graph/agents/api。
# 即插即用: 由 API 端点手动触发 或 lifespan 后台定时任务; 不触发则不运行(零影响)。
#
# 对应设计文档 docs/MEMORY_LAYER_DESIGN.md §8(记忆整合) + §8.2(时间衰减已在store.search内)。

from typing import Optional

from src.core.logging import log


class MemoryConsolidator:
    """记忆整合器 — 即插即用组件。

    用法:
        consolidator = MemoryConsolidator(long_term_store)
        stats = await consolidator.consolidate_user("user-1")
        # stats = {"deduped": 2, "abstracted": 1}

    触发: API 端点(手动) 或 lifespan 后台定时循环。失败降级, 不影响主流程。
    """

    DEDUP_THRESHOLD = 0.88  # ponytail: 默认值, BGE-M3中文短文本cosine偏低, 0.92过严(实测几乎不触发)
    CLUSTER_THRESHOLD = 0.6  # C2: 主题聚类阈值(比去重宽, 同主题归组而非判重)
    MIN_EPISODIC_TO_ABSTRACT = 3  # 情节记忆少于此数不值得提炼

    def __init__(self, long_term_store=None, dedup_threshold: float | None = None):
        self.store = long_term_store
        # ponytail: dedup_threshold 可注入(config调优); None则用类默认
        self.dedup_threshold = dedup_threshold if dedup_threshold is not None else self.DEDUP_THRESHOLD
        # F3: 整合运行状态(可观测)
        self._status: dict = {"running": False, "last_run_at": None,
                              "last_stats": {}, "run_count": 0, "last_error": None}

    def get_status(self) -> dict:
        """F3: 返回整合器运行状态(供 /memory/consolidate/status 端点)。"""
        return dict(self._status)

    async def consolidate_user(self, user_id: str) -> dict:
        """整合单个用户的记忆(去重 + 抽象提炼)。返回统计。"""
        if self.store is None:
            return {"skipped": True}
        stats = {"deduped": 0, "abstracted": 0}
        try:
            stats["deduped"] = await self._deduplicate(user_id)
            stats["abstracted"] = await self._abstract_episodic(user_id)
            log.info("memory_consolidated", user_id=user_id, **stats)
        except Exception as e:
            log.warning("memory_consolidate_failed", user_id=user_id, error=type(e).__name__, msg=str(e))
        return stats

    async def consolidate_all(self, user_ids: Optional[list[str]] = None) -> dict:
        """批量整合。user_ids=None 时从 store 提取所有有记忆的 user_id(扫描)。

        F3: 包裹 running/last_run_at/last_stats/run_count 状态更新。
        Returns:
            dict: {user_count, total_deduped, total_abstracted}
        """
        if self.store is None:
            return {"skipped": True}
        from datetime import datetime
        self._status["running"] = True
        self._status["last_error"] = None
        try:
            if user_ids is None:
                user_ids = await self._collect_user_ids()
            total_d, total_a = 0, 0
            for uid in user_ids:
                s = await self.consolidate_user(uid)
                total_d += s.get("deduped", 0)
                total_a += s.get("abstracted", 0)
            result = {"user_count": len(user_ids), "total_deduped": total_d, "total_abstracted": total_a}
            self._status["last_stats"] = result
            self._status["run_count"] = self._status.get("run_count", 0) + 1
            log.info("memory_consolidate_all", users=len(user_ids), deduped=total_d, abstracted=total_a)
            return result
        except Exception as e:
            self._status["last_error"] = f"{type(e).__name__}: {e}"
            log.warning("memory_consolidate_all_failed", error=type(e).__name__, msg=str(e))
            return {"error": str(e)}
        finally:
            self._status["running"] = False
            self._status["last_run_at"] = datetime.now().isoformat()

    async def _deduplicate(self, user_id: str) -> int:
        """去重: 每条记忆检索相似记忆, 相似度>阈值则删内容更短的一方。

        ponytail: O(n×k) 扫描, 记忆库规模可控时足够; 超大库可改批量聚类(留作升级路径)。
        """
        memories = await self.store.list_all(user_id)
        if len(memories) < 2:
            return 0
        deleted: set[str] = set()
        for mem in memories:
            mid = mem["id"]
            if mid in deleted or not mem["content"]:
                continue
            # 用此记忆内容检索相似记忆(带原始相似度分数)
            similar = await self.store.search_with_scores(user_id, mem["content"], top_k=5)
            for s in similar:
                sid = s["id"]
                if sid == mid or sid in deleted:
                    continue
                if s["score"] >= self.dedup_threshold:
                    # 冲突消解: 保留内容更全(更长)的一方, 删冗余
                    victim, keeper = (sid, mid) if len(s["content"]) < len(mem["content"]) else (mid, sid)
                    if victim not in deleted:
                        await self.store.delete_one(victim)
                        deleted.add(victim)
                        log.info("memory_deduped", user_id=user_id, deleted=victim[:12],
                                 keeper=keeper[:12], score=round(s["score"], 3))
        return len(deleted)

    async def _abstract_episodic(self, user_id: str) -> int:
        """抽象提炼: 情节记忆 → 语义记忆(LLM总结规律)。

        C2: 先按主题聚类(避免混主题杂糅), 每组单独抽象。
        ponytail: 情节记忆≥阈值条才提炼; 提炼产出语义记忆, 原始情节保留(可溯源)。
        """
        episodics = await self.store.list_all(user_id, memory_type="episodic")
        if len(episodics) < self.MIN_EPISODIC_TO_ABSTRACT:
            return 0
        contents = [m["content"] for m in episodics if m["content"]]
        if not contents:
            return 0

        # C2: 按主题聚类, 避免把"爬虫反爬"和"OSS部署"等不同主题强行归纳
        clusters = await self._cluster_by_topic(contents)
        written = 0
        for cluster in clusters:
            if len(cluster) < 2:
                continue  # ponytail: 单条孤立情节不值得抽象, 跳过
            summary = await self._abstract_llm(cluster)
            if not summary or summary.strip().upper() == "NONE":
                continue
            # 每行作为一条独立语义记忆写入
            for line in summary.strip().splitlines():
                line = line.strip("- •·\t ")
                if len(line) < 4:
                    continue
                mid = await self.store.add(
                    user_id, line, memory_type="semantic",
                    metadata={"abstracted_from": len(cluster)},
                )
                if mid:
                    written += 1
        if written:
            log.info("memory_abstracted", user_id=user_id,
                     clusters=len(clusters), from_episodic=len(contents), to_semantic=written)
        return written

    async def _cluster_by_topic(self, contents: list[str]) -> list[list[str]]:
        """C2: 语义贪心聚类。embed 每条 → 两两相似度>阈值归同组。

        ponytail: O(n²) 两两比对, 记忆库规模可控; 超大库可改 ANN(留作升级)。
        返回 [[同主题条目], ...]。
        """
        if not self.store or len(contents) <= 1:
            return [list(contents)] if contents else []
        try:
            vecs = [await self.store.embed(c) for c in contents]
        except Exception as e:
            log.warning("memory_cluster_embed_failed", error=str(e))
            return [list(contents)]  # embed 失败降级: 全部一组

        visited = [False] * len(contents)
        clusters: list[list[str]] = []
        for i in range(len(contents)):
            if visited[i]:
                continue
            group = [contents[i]]
            visited[i] = True
            for j in range(i + 1, len(contents)):
                if visited[j]:
                    continue
                sim = self._cosine(vecs[i], vecs[j])
                if sim >= self.CLUSTER_THRESHOLD:
                    group.append(contents[j])
                    visited[j] = True
            clusters.append(group)
        return clusters

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        """余弦相似度(聚类用)。"""
        import math
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        return dot / (na * nb) if na and nb else 0.0

    async def _abstract_llm(self, episodes: list[str]) -> Optional[str]:
        """LLM 抽象提炼。走 fast purpose。失败返回 None。"""
        try:
            from src.core.llm import chat
            from src.prompts import ABSTRACT_PROMPT
            text = "\n".join(f"- {e}" for e in episodes[:20])  # ponytail: 截断控成本
            return await chat([{"role": "user", "content": ABSTRACT_PROMPT.format(episodes=text)}])
        except Exception as e:
            log.warning("memory_abstract_llm_failed", error=type(e).__name__, msg=str(e))
            return None

    async def _collect_user_ids(self) -> list[str]:
        """扫描 store 收集所有 user_id(批量整合用)。

        ponytail: scroll payload 取 user_id 去重; 记忆库大时可改 Qdrant facet(留作升级)。
        """
        try:
            collected, offset = set(), None
            while True:
                batch, offset = await self.store.client.scroll(
                    self.store.collection_name, limit=100, offset=offset,
                    with_payload=True, with_vectors=False,
                )
                if not batch:
                    break
                for p in batch:
                    uid = (p.payload or {}).get("user_id")
                    if uid:
                        collected.add(uid)
                if offset is None:
                    break
            return list(collected)
        except Exception as e:
            log.warning("memory_collect_users_failed", error=str(e))
            return []


__all__ = ["MemoryConsolidator"]

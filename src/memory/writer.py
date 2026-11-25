# 记忆写入器 — P2 核心
#
# 职责: 任务结束后, 从一轮问答中抽取信息并沉淀为长期记忆(实体记忆 user_facts + 长期记忆 agent_memory)。
# 解耦: 纯组件, 编排 fact_store + long_term_store + LLM; 不依赖 graph/agents/api。
# 即插即用: 由 lifespan 按配置注入; query.py 响应后 create_task 调用, 失败降级不阻塞。
#
# 对应设计文档 docs/MEMORY_LAYER_DESIGN.md §7.2(写阶段) + §6.1(结构化抽取压缩)。
# 注: 一次 LLM 调用同时完成"抽取事实+判断价值+摘要"(三合一), 省 2 次调用。


from src.core.logging import log


class MemoryWriter:
    """记忆写入器 — 即插即用组件。

    用法:
        writer = MemoryWriter(fact_store, long_term_store)
        await writer.write("user-1", "用什么框架?", "推荐FastAPI...", source="kb_grounded")

    行为:
        1. LLM 三合一抽取: facts(结构化) + memorable(是否值得存) + summary(摘要)
        2. facts → user_facts 表(实体记忆, upsert 冲突消解)
        3. memorable+summary → agent_memory(长期记忆, embed 检索)
        全程 try/except, 任一失败降级, 不抛(写闭环是尽力而为)。
    """

    def __init__(
        self,
        fact_store=None,
        long_term_store=None,
        enabled: bool = True,
    ):
        self.fact_store = fact_store
        self.long_term_store = long_term_store
        self.enabled = enabled

    async def write(self, user_id: str, question: str, answer: str, source: str = "") -> dict:
        """从一轮问答沉淀记忆。返回写入统计(可观测)。失败不抛。

        Args:
            user_id: 用户ID(记忆隔离)
            question: 用户问题
            answer: 助手回答
            source: 答案来源(溯源元数据)
        """
        if not self.enabled or not question or not answer:
            return {"skipped": True}

        stats = {"facts_written": 0, "long_term_written": False}

        try:
            res = await self._extract(question, answer)
        except Exception as e:
            log.warning("memory_extract_failed", user_id=user_id, error=type(e).__name__, msg=str(e))
            return stats

        # ① 写实体记忆(user_facts) — 结构化抽取压缩法
        facts = res.get("facts") or {}
        if facts and self.fact_store:
            for k, v in facts.items():
                if k and v is not None:
                    await self.fact_store.upsert(user_id, k, str(v))
                    stats["facts_written"] += 1

        # ② 写长期记忆(agent_memory) — 仅当 memorable 且有 summary
        summary = (res.get("summary") or "").strip()
        if res.get("memorable") and summary and self.long_term_store:
            stats["long_term_written"] = await self._write_long_term(user_id, summary, source)

        log.info(
            "memory_written", user_id=user_id,
            facts=stats["facts_written"], long_term=stats["long_term_written"],
            memorable=res.get("memorable"), source=source,
        )
        return stats

    async def _write_long_term(self, user_id: str, summary: str, source: str) -> bool:
        """B1: 写长期记忆(带写前查重)。score≥0.95 覆盖近乎重复的, 否则新增。

        防并发写相似内容(同用户连续表达同一偏好时, 覆盖而非累积)。
        """
        WRITE_DEDUP_THRESHOLD = 0.95  # ponytail: 高阈值, 仅近乎重复才覆盖, 避免误并
        try:
            existing = await self.long_term_store.search_with_scores(user_id, summary, top_k=1)
            if existing and existing[0].get("score", 0) >= WRITE_DEDUP_THRESHOLD:
                # 近乎重复 → 覆盖(更新内容, 保留id)
                return await self.long_term_store.update(
                    existing[0]["id"], content=summary,
                )
        except Exception as e:
            log.warning("memory_write_dedup_check_failed", error=type(e).__name__, msg=str(e))
        # 新增
        mem_id = await self.long_term_store.add(
            user_id, summary, memory_type="episodic", metadata={"source": source}
        )
        return mem_id is not None

    async def _extract(self, question: str, answer: str) -> dict:
        """LLM 三合一抽取: facts + memorable + summary。走 fast purpose(轻量)。"""
        from src.core.llm import chat_json
        from src.prompts import FACT_EXTRACT_PROMPT

        prompt = FACT_EXTRACT_PROMPT.format(question=question, answer=answer[:1500])  # ponytail: 截断长答案控成本
        res = await chat_json([{"role": "user", "content": prompt}])
        # 防御: 确保字段类型
        return {
            "facts": res.get("facts") if isinstance(res.get("facts"), dict) else {},
            "memorable": bool(res.get("memorable", False)),
            "summary": str(res.get("summary", "")),
        }


__all__ = ["MemoryWriter"]

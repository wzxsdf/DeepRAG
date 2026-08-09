# 长期记忆存储组件 — P1 核心
#
# 职责: 跨会话的语义记忆, 写入 + 语义检索, 复用 BGE-M3(1024维) + Qdrant(独立 agent_memory collection)。
# 解耦: 纯组件, embedder 可注入(默认复用 retrieval.embedder), 不依赖 graph/agents/api。
# 即插即用: 由 lifespan 按配置注入; 不注入则 graph 读闭环自动跳过(向后兼容)。
#
# 对应设计文档 docs/MEMORY_LAYER_DESIGN.md §4.1(长期记忆 Qdrant collection) + §7.2(读闭环)。
# 注: P1 含最小写入能力(供闭环验证); 自动写入(graph终止节点沉淀)属 P2。

import asyncio
import uuid
import math
from datetime import datetime
from typing import Callable, Awaitable, Optional, Any

from qdrant_client import QdrantClient
from qdrant_client.models import VectorParams, Distance, PointStruct, Filter, FieldCondition, MatchValue

from src.core.config import QdrantCfg
from src.core.logging import log


# 记忆向量维度 = BGE-M3 输出维度
EMBED_DIM = 1024

# 时间衰减: exp(-λ×天数), λ=0.05 → 约14天衰减到一半(近期记忆优先)
DECAY_LAMBDA = 0.05

# E4 分类型衰减系数(×decay_lambda): semantic=规律不该被时间冲淡; episodic=事件会过时; procedural=流程半衰减
TYPE_DECAY_FACTOR = {"semantic": 0.0, "episodic": 1.0, "procedural": 0.5}

# embedder 签名: 输入文本 → 返回向量 list[float]。可注入(测试/换模型)。
Embedder = Callable[[str], Awaitable[list[float]]]


async def default_embedder(text: str) -> list[float]:
    """默认嵌入器: 复用 retrieval.embedder.embed_query(BGE-M3, 与知识库检索同源)。

    ponytail: 延迟 import 避免循环; 记忆向量与 KB 向量同模型同维度, 可共存于同一 Qdrant 实例。
    """
    from src.retrieval.embedder import embed_query
    return await embed_query(text)


class LongTermMemoryStore:
    """长期记忆存储 — 即插即用组件。

    用法:
        store = LongTermMemoryStore(qdrant_cfg)
        await store.add("user-1", "用户偏好Python简洁风格", memory_type="semantic")
        memories = await store.search("user-1", "代码风格偏好", top_k=3)

    存储结构(Qdrant agent_memory collection):
        vector: {dense: 1024维}        # BGE-M3 语义索引
        payload: {user_id, memory_type, content, created_at, ...}
    """

    def __init__(
        self,
        qdrant_cfg: Optional[QdrantCfg] = None,
        embedder: Optional[Embedder] = None,
        collection_name: str = "agent_memory",
        client: Optional[QdrantClient] = None,  # 可注入(测试mock/复用已有连接)
        time_decay: bool = True,  # P3: 检索时启用时间衰减(近期记忆优先)
        decay_lambda: float = DECAY_LAMBDA,
        max_per_user: int = 0,  # E1: 单用户记忆上限(0=不限); 超限LRU淘汰(access_count低+老)
        host: str = "http://localhost",
        port: int = 6333,
        api_key: str = "",
    ):
        if client is not None:
            self.client = client
        else:
            if qdrant_cfg is not None:  # ponytail: cfg注入, 旧kwargs向后兼容
                host, port, api_key = qdrant_cfg.host, qdrant_cfg.port, qdrant_cfg.api_key
            # ponytail: host无scheme时补http(QdrantWriter._normalize_host同款, 不复用避免跨模块耦合)
            if not host.startswith("http://") and not host.startswith("https://"):
                host = "http://" + host
            self.client = QdrantClient(url=f"{host.rstrip('/')}:{port}", api_key=api_key or None)
        self.collection_name = collection_name
        self._embed = embedder or default_embedder
        self.time_decay = time_decay
        self.decay_lambda = decay_lambda
        self.max_per_user = max_per_user
        self._ensure_collection()

    def _ensure_collection(self):
        """幂等创建 agent_memory collection(dense 1024维 cosine)。独立于知识库 chunks collection。"""
        if not self.client.collection_exists(self.collection_name):
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config={"dense": VectorParams(size=EMBED_DIM, distance=Distance.COSINE)},
            )
            log.info("long_term_memory_collection_created", collection=self.collection_name, dim=EMBED_DIM)

    async def add(
        self,
        user_id: str,
        content: str,
        memory_type: str = "episodic",  # episodic(情节) / semantic(语义) / procedural(程序)
        metadata: Optional[dict] = None,
        memory_id: Optional[str] = None,
        importance: float = 0.5,  # D1 重要性[0,1], 淘汰/排序参考
        valid_until: Optional[datetime] = None,  # D4 有效期(None=永久); 过期后不检索
    ) -> Optional[str]:
        """写入一条长期记忆。返回 memory_id(失败返回 None)。

        Args:
            user_id: 用户ID(记忆隔离键)
            content: 记忆内容(一次完整交互/一个知识点, 见设计文档粒度原则)
            memory_type: 记忆类型
            metadata: 额外元数据(source_conv 等)
            memory_id: 指定ID(默认随机; 显式指定可幂等覆盖)
            importance: 重要性[0,1]
            valid_until: 过期时间(None=永久)
        """
        content = (content or "").strip()
        if not content:
            return None
        try:
            vec = await self._embed(content)
            # ponytail: Qdrant point id 必须是 uint 或完整UUID(自定义前缀字符串会被拒, 真实环境校验)
            mem_id = memory_id or str(uuid.uuid4())
            now = datetime.now()
            payload: dict[str, Any] = {
                "user_id": user_id,
                "memory_type": memory_type,
                "content": content,
                "created_at": now.isoformat(),
                "importance": importance,                           # D1
                "access_count": 0,                                   # D2 命中次数(检索时递增)
                "last_accessed": now.isoformat(),                    # D3 最后访问(检索时更新)
                "valid_until": valid_until.isoformat() if valid_until else None,  # D4
                **(metadata or {}),
            }
            await asyncio.to_thread(
                self.client.upsert,
                collection_name=self.collection_name,
                points=[PointStruct(id=mem_id, vector={"dense": vec}, payload=payload)],
            )
            # E1: 超容量则LRU淘汰(刚写入的不被淘汰, 因 access_count=0 但 last_accessed 最新)
            await self._evict_if_needed(user_id)
            log.info("long_term_memory_added", user_id=user_id, memory_type=memory_type,
                     memory_id=mem_id, content_len=len(content))
            return mem_id
        except Exception as e:
            log.warning("long_term_memory_add_failed", user_id=user_id, error=type(e).__name__, msg=str(e))
            return None

    async def search(
        self,
        user_id: str,
        query: str,
        top_k: int = 3,
        memory_type: Optional[str] = None,
    ) -> list[str]:
        """语义检索长期记忆。返回 content 列表(按相似度降序)。

        P3: 若 time_decay=True, 取 top_k×4 候选 → 按相似度×新鲜度重排 → 取 top_k(近期优先)。
        """
        ranked = await self.search_with_scores(user_id, query, top_k=top_k, memory_type=memory_type)
        return [r["content"] for r in ranked]

    async def search_with_scores(
        self,
        user_id: str,
        query: str,
        top_k: int = 3,
        memory_type: Optional[str] = None,
    ) -> list[dict]:
        """语义检索, 返回富列表 [{id, content, score, created_at, memory_type, access_count, final_score}]。

        E4: 分类型衰减(semantic不衰减, episodic全衰减, procedural半衰减)。
        D4: 过滤 valid_until 已过期的记忆。
        D2/D3: 命中后异步递增 access_count + 更新 last_accessed。
        """
        try:
            vec = await self._embed(query)
            must = [FieldCondition(key="user_id", match=MatchValue(value=user_id))]
            if memory_type:
                must.append(FieldCondition(key="memory_type", match=MatchValue(value=memory_type)))
            # ponytail: 启用衰减时多取候选再重排; 否则直接取 top_k
            limit = top_k * 4 if self.time_decay else top_k
            res = await asyncio.to_thread(
                self.client.query_points,
                self.collection_name, query=vec, using="dense",
                query_filter=Filter(must=must), limit=limit, with_payload=True,
            )
            now = datetime.now()
            items: list[dict] = []
            for p in res.points:
                payload = p.payload or {}
                # D4 过期过滤: valid_until 早于 now 的跳过
                vu = self._parse_time(payload.get("valid_until"))
                if vu and vu < now:
                    continue
                created_at = self._parse_time(payload.get("created_at"))
                mtype = payload.get("memory_type", "episodic")
                # E4 分类型衰减: semantic=规律不衰减; episodic=事件衰减; procedural=半衰减
                final = p.score
                if self.time_decay and created_at:
                    days = max((now - created_at).total_seconds() / 86400, 0)
                    factor = TYPE_DECAY_FACTOR.get(mtype, 1.0)
                    final = p.score * math.exp(-self.decay_lambda * factor * days)
                items.append({
                    "id": str(p.id),
                    "content": payload.get("content", ""),
                    "score": p.score,
                    "created_at": created_at,
                    "memory_type": mtype,
                    "access_count": payload.get("access_count", 0),
                    "final_score": final,
                })
            # 按 final_score 降序
            items.sort(key=lambda x: x["final_score"], reverse=True)
            top = items[:top_k]
            # D2/D3: 异步递增命中记忆的 access_count + last_accessed(fire-and-forget, 偶发并发偏差可接受)
            if top:
                asyncio.create_task(self._touch_accessed(top))
            return top
        except Exception as e:
            log.warning("long_term_memory_search_failed", user_id=user_id, error=type(e).__name__, msg=str(e))
            return []

    async def _touch_accessed(self, items: list[dict]) -> None:
        """D2/D3: 批量递增命中记忆的 access_count + 更新 last_accessed。

        ponytail: set_payload 非原子(并发偶发丢+1), 记忆计数场景可容忍; 失败静默(不影响检索)。
        """
        now_iso = datetime.now().isoformat()
        for it in items:
            try:
                new_count = (it.get("access_count") or 0) + 1
                await asyncio.to_thread(
                    self.client.set_payload,
                    collection_name=self.collection_name,
                    payload={"access_count": new_count, "last_accessed": now_iso},
                    points=[it["id"]],
                )
            except Exception:
                pass  # 计数失败不影响主流程

    @staticmethod
    def _parse_time(ts) -> Optional[datetime]:
        """解析 ISO 时间字符串, 失败返回 None。"""
        if not ts:
            return None
        try:
            return datetime.fromisoformat(str(ts))
        except Exception:
            return None

    async def list_all(self, user_id: str, memory_type: Optional[str] = None, limit: int = 200) -> list[dict]:
        """列出某用户全部记忆(整合器遍历用)。scroll API 翻页取。

        Returns:
            list[dict]: [{id, content, memory_type, created_at}]
        """
        try:
            must = [FieldCondition(key="user_id", match=MatchValue(value=user_id))]
            if memory_type:
                must.append(FieldCondition(key="memory_type", match=MatchValue(value=memory_type)))
            collected, offset = [], None
            while len(collected) < limit:
                batch, offset = await asyncio.to_thread(
                    self.client.scroll,
                    self.collection_name,
                    scroll_filter=Filter(must=must),
                    limit=min(100, limit - len(collected)),
                    offset=offset,
                    with_payload=True, with_vectors=False,
                )
                if not batch:
                    break
                for p in batch:
                    payload = p.payload or {}
                    collected.append({
                        "id": str(p.id),
                        "content": payload.get("content", ""),
                        "memory_type": payload.get("memory_type", ""),
                        "created_at": self._parse_time(payload.get("created_at")),
                        "last_accessed": self._parse_time(payload.get("last_accessed")),
                        "access_count": payload.get("access_count", 0),
                        "importance": payload.get("importance", 0.5),
                    })
                if offset is None:
                    break
            return collected
        except Exception as e:
            log.warning("long_term_memory_list_failed", user_id=user_id, error=type(e).__name__, msg=str(e))
            return []

    async def delete_one(self, memory_id: str) -> None:
        """按 id 删除单条记忆(去重时删冗余)。幂等。"""
        try:
            await asyncio.to_thread(
                self.client.delete,
                collection_name=self.collection_name,
                points_selector=[memory_id],
            )
        except Exception as e:
            log.warning("long_term_memory_delete_one_failed", memory_id=memory_id, error=str(e))

    async def update(
        self,
        memory_id: str,
        content: Optional[str] = None,
        importance: Optional[float] = None,
        valid_until: Optional[datetime] = None,
        clear_valid_until: bool = False,
    ) -> bool:
        """F2: 部分更新记忆。返回 True=成功。

        - content 改 → 重 embed + upsert(保留其他 payload 字段)
        - importance/valid_until → set_payload 部分更新
        - clear_valid_until=True → valid_until 置 None(取消过期)
        """
        try:
            if content is not None:
                content = content.strip()
                if not content:
                    return False
                # content 改需重 embed; 先取原 payload 保留其他字段
                pts = await asyncio.to_thread(
                    self.client.retrieve, self.collection_name, [memory_id],
                    with_payload=True, with_vectors=False,
                )
                if not pts:
                    return False
                payload = dict(pts[0].payload or {})
                payload["content"] = content
                if importance is not None:
                    payload["importance"] = importance
                if clear_valid_until:
                    payload["valid_until"] = None
                elif valid_until is not None:
                    payload["valid_until"] = valid_until.isoformat()
                vec = await self._embed(content)
                await asyncio.to_thread(
                    self.client.upsert, self.collection_name,
                    [PointStruct(id=memory_id, vector={"dense": vec}, payload=payload)],
                )
            else:
                upd: dict[str, Any] = {}
                if importance is not None:
                    upd["importance"] = importance
                if clear_valid_until:
                    upd["valid_until"] = None
                elif valid_until is not None:
                    upd["valid_until"] = valid_until.isoformat()
                if not upd:
                    return False
                await asyncio.to_thread(
                    self.client.set_payload, self.collection_name, upd, [memory_id],
                )
            return True
        except Exception as e:
            log.warning("long_term_memory_update_failed", memory_id=memory_id, error=type(e).__name__, msg=str(e))
            return False

    async def embed(self, text: str) -> list[float]:
        """C2/B1 共用: 公开嵌入接口(包装_embed)。供 consolidator 聚类/writer 查重。"""
        return await self._embed(text)

    async def _evict_if_needed(self, user_id: str) -> None:
        """E1: LRU淘汰。单用户记忆超 max_per_user 时, 淘汰 access_count 低 + 最久未访问的。

        ponytail: O(n log n) 排序, 记忆库规模可控; max_per_user=0 不启用。新写入的不易被淘汰
        (虽 access_count=0, 但 last_accessed=now 最新)。
        """
        if not self.max_per_user:
            return
        try:
            items = await self.list_all(user_id, limit=self.max_per_user + 50)
            if len(items) <= self.max_per_user:
                return

            def _evict_key(it):
                # 升序: access_count 低 → last_accessed/created 老 → 先淘汰
                last = it.get("last_accessed") or it.get("created_at") or datetime.min
                return (it.get("access_count", 0), last)
            items.sort(key=_evict_key)
            excess = len(items) - self.max_per_user
            for it in items[:excess]:
                await self.delete_one(it["id"])
            log.info("long_term_memory_evicted", user_id=user_id, count=excess)
        except Exception as e:
            log.warning("long_term_memory_evict_failed", user_id=user_id, error=str(e))

    async def count(self, user_id: Optional[str] = None) -> int:
        """记忆总数(F1 指标用)。user_id 指定时只计该用户; 失败返回 -1。"""
        try:
            flt = Filter(must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))]) if user_id else None
            res = await asyncio.to_thread(
                self.client.count, self.collection_name, count_filter=flt, exact=True
            )
            return res.count
        except Exception as e:
            log.warning("long_term_memory_count_failed", error=str(e))
            return -1

    async def delete_by_user(self, user_id: str) -> None:
        """删除某用户全部记忆(隐私/注销)。幂等。"""
        try:
            await asyncio.to_thread(
                self.client.delete,
                collection_name=self.collection_name,
                points_selector=Filter(must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))]),
            )
            log.info("long_term_memory_cleared", user_id=user_id)
        except Exception as e:
            log.warning("long_term_memory_clear_failed", user_id=user_id, error=str(e))


__all__ = ["LongTermMemoryStore", "default_embedder", "EMBED_DIM"]

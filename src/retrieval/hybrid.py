# ponytail: hybrid检索 - dense(BGE-M3) + Qdrant原生BM25 + 加权RRF, 命中后从OSS拉文本+父块

import asyncio
from typing import List
from dotenv import load_dotenv

from src.storage.protocol import RetrievalFilter, RetrievedDocument
from src.storage.qdrant_store import QdrantWriter
from src.storage.mysql import MySQLStore
from src.storage.oss import OssStore
from src.retrieval.embedder import embed_query
from src.core.config import Settings, get_settings

load_dotenv()


class HybridRetriever:
    """实现 Retriever Protocol. dense + BM25 服务端加权 RRF 融合."""

    def __init__(self, qdrant=None, oss=None, mysql=None, settings=None):
        # ponytail: 阶段5 — 存储实例注入(依赖倒置, lifespan建共享实例); None则settings兜底(兼容scripts)
        self._settings = settings or get_settings()
        self.qdrant = qdrant or QdrantWriter(cfg=self._settings.qdrant_cfg)
        self.oss = oss or OssStore(cfg=self._settings.oss_cfg)
        self._mysql_store = mysql or MySQLStore(cfg=self._settings.mysql_cfg)

    async def retrieve(self, query: str, filters: RetrievalFilter, top_k: int) -> List[RetrievedDocument]:
        dense = await embed_query(query)
        res = await self.qdrant.query_hybrid_async(
            dense_vec=dense, query_text=query, tenant_id=filters.tenant_id, kb_ids=filters.kb_ids, limit=top_k,
        )

        # ponytail: content优先取Qdrant payload(零IO); 仅老数据(无content payload)才查MySQL回拉OSS。
        # parent内容仍从OSS取(parent_id在payload, 大文本不入payload)。新数据路径零MySQL、零content-IO。
        async def _get(key: str) -> str:
            try:
                return (await self.oss.get(key)).decode(errors="replace")
            except Exception:
                return ""

        parent_ids = {(p.payload or {}).get("parent_id") for p in res}
        parent_ids.discard(None)
        parent_tasks = {pid: asyncio.ensure_future(_get(f"parent_chunks/{pid}.txt")) for pid in parent_ids}

        # 老数据fallback: payload无content的chunk才查MySQL取oss_key再拉OSS(新数据此块跳过)
        need_mysql = [str(p.id) for p in res if not (p.payload or {}).get("content")]
        content_tasks: dict[str, asyncio.Task] = {}
        if need_mysql:
            # ponytail: 共享mysql已connect(lifespan)或lazy connect(scripts); 不每次connect/close
            if self._mysql_store.pool is None:
                await self._mysql_store.connect()
            ph = ",".join(["%s"] * len(need_mysql))
            rows = await self._mysql_store.fetch(f"SELECT id, oss_key FROM chunks WHERE id IN ({ph})", *need_mysql)
            for r in rows:
                if r.get("oss_key"):
                    content_tasks[r["id"]] = asyncio.ensure_future(_get(r["oss_key"]))

        pmap = dict(zip(parent_tasks.keys(), await asyncio.gather(*parent_tasks.values()))) if parent_tasks else {}
        cmap = dict(zip(content_tasks.keys(), await asyncio.gather(*content_tasks.values()))) if content_tasks else {}

        docs: List[RetrievedDocument] = []
        for p in res:
            pl = p.payload or {}
            cid = str(p.id)
            content = pl.get("content") or cmap.get(cid, "")
            docs.append(RetrievedDocument(
                chunk_id=cid,
                document_id=pl.get("document_id", ""),
                version_id=pl.get("version_id", ""),
                parent_id=pl.get("parent_id"),
                content=content,
                locator={"page": pl.get("page", 0), "heading_path": pl.get("heading_path", [])},
                score=p.score,
                retrieval_source="hybrid_rrf",
                parent_content=pmap.get(pl.get("parent_id"), ""),
            ))
        return docs

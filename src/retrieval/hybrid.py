# ponytail: hybrid检索 - dense(BGE-M3) + Qdrant原生BM25 + 加权RRF, 命中后从OSS拉文本+父块

import os
import asyncio
from typing import List
from dotenv import load_dotenv

from src.storage.protocol import RetrievalFilter, RetrievedDocument
from src.storage.qdrant_store import QdrantWriter
from src.storage.mysql import MySQLStore
from src.storage.oss import OssStore
from src.retrieval.embedder import embed_query

load_dotenv()


class HybridRetriever:
    """实现 Retriever Protocol. dense + BM25 服务端加权 RRF 融合."""

    def __init__(self):
        self.qdrant = QdrantWriter(
            host=os.getenv("QDRANT_HOST", ""),
            port=int(os.getenv("QDRANT_PORT", "6333")),
            api_key=os.getenv("QDRANT_API_KEY", ""),
        )
        self.oss = OssStore(
            endpoint=os.getenv("OSS_ENDPOINT", ""),
            bucket=os.getenv("OSS_BUCKET", ""),
            access_key_id=os.getenv("OSS_ACCESS_KEY_ID", ""),
            access_key_secret=os.getenv("OSS_ACCESS_KEY_SECRET", ""),
        )

    def _mysql(self):
        # ponytail: 每次retrieve独立MySQLStore, 避免并行(多跳asyncio.gather)时共享pool的close竞争
        return MySQLStore(
            host=os.getenv("MYSQL_HOST", ""),
            port=int(os.getenv("MYSQL_PORT", "3306")),
            db=os.getenv("MYSQL_DATABASE", os.getenv("MYSQL_DB", "deeprag")),
            user=os.getenv("MYSQL_USER", ""),
            password=os.getenv("MYSQL_PASSWORD", ""),
        )

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
            mysql = self._mysql()
            await mysql.connect()
            try:
                ph = ",".join(["%s"] * len(need_mysql))
                rows = await mysql.fetch(f"SELECT id, oss_key FROM chunks WHERE id IN ({ph})", *need_mysql)
                for r in rows:
                    if r.get("oss_key"):
                        content_tasks[r["id"]] = asyncio.ensure_future(_get(r["oss_key"]))
            finally:
                await mysql.close()

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

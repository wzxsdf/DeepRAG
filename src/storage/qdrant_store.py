# ponytail: W2 hybrid检索 - dense(BGE-M3) + sparse(Qdrant原生BM25) + 加权RRF(dense主导)

import asyncio
from typing import List

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct, SparseVectorParams, Modifier,
    Document, Filter, FieldCondition, MatchValue, MatchAny,
)
from qdrant_client.hybrid.fusion import reciprocal_rank_fusion

from src.core.config import QdrantCfg


# ponytail: 服务端是http, host无scheme时qdrant_client默认https会SSL:WRONG_VERSION_NUMBER
def _normalize_host(host: str) -> str:
    if not host.startswith("http://") and not host.startswith("https://"):
        host = "http://" + host
    return host.rstrip("/")


class QdrantWriter:
    def __init__(self, cfg: QdrantCfg | None = None, host: str = "http://localhost", port: int = 6333, api_key: str = ""):
        if cfg is not None:  # ponytail: cfg注入(阶段1), 旧kwargs向后兼容
            host, port, api_key = cfg.host, cfg.port, cfg.api_key
        self.client = QdrantClient(url=f"{_normalize_host(host)}:{port}", api_key=api_key or None)
        self.collection_name = "chunks"

    @staticmethod
    def _build_filter(
        tenant_id: str,
        kb_ids: List[str] | None = None,
        acl_tags: List[str] | None = None,
        active_version_id: str | None = None,
        active_version_ids: List[str] | None = None,
    ) -> Filter:
        """构造 dense/sparse 共用的召回前过滤条件."""
        must = [FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id))]
        if kb_ids:
            must.append(FieldCondition(key="kb_id", match=MatchAny(any=kb_ids)))
        if acl_tags:
            # 文档必须包含用户拥有的每个标签，避免 MatchAny 放宽权限边界。
            must.extend(FieldCondition(key="acl_tags", match=MatchValue(value=tag)) for tag in acl_tags)
        if active_version_id:
            must.append(FieldCondition(key="version_id", match=MatchValue(value=active_version_id)))
        elif active_version_ids is not None:
            if not active_version_ids:
                must.append(FieldCondition(key="version_id", match=MatchValue(value="__no_active_version__")))
            else:
                must.append(FieldCondition(key="version_id", match=MatchAny(any=active_version_ids)))
        return Filter(must=must)

    def ensure_collection(self):
        if not self.client.collection_exists(self.collection_name):
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config={"dense": VectorParams(size=1024, distance=Distance.COSINE)},
                sparse_vectors_config={"sparse": SparseVectorParams(modifier=Modifier.IDF)},
            )

    def recreate_collection(self):
        try:
            self.client.delete_collection(self.collection_name)
        except Exception:
            pass
        self.ensure_collection()

    async def insert_chunks(self, chunks: List[dict]):
        points = [
            PointStruct(
                id=chunk["id"],
                vector={"dense": chunk["dense_vector"],
                        "sparse": Document(text=chunk["sparse_text"], model="Qdrant/bm25")},
                payload=chunk["payload"],
            )
            for chunk in chunks
        ]
        self.client.upsert(collection_name=self.collection_name, points=points)

    def query_hybrid(self, dense_vec: List[float], query_text: str,
                     tenant_id: str = "default", kb_ids: List[str] | None = None, limit: int = 10,
                     acl_tags: List[str] | None = None, active_version_id: str | None = None,
                     active_version_ids: List[str] | None = None):
        """hybrid检索: dense+sparse并使用同一组召回前过滤条件."""
        query_filter = self._build_filter(
            tenant_id, kb_ids, acl_tags, active_version_id, active_version_ids)
        dense_res = self.client.query_points(self.collection_name, query=dense_vec, using="dense",
            query_filter=query_filter, limit=limit * 3, with_payload=True)
        sparse_res = self.client.query_points(self.collection_name,
            query=Document(text=query_text, model="Qdrant/bm25"), using="sparse",
            query_filter=query_filter, limit=limit * 3, with_payload=True)
        # ponytail: 加权RRF, dense权重0.65主导, 避免BM25共有词噪声拉低排序
        return reciprocal_rank_fusion(responses=[dense_res.points, sparse_res.points],
                                       limit=limit, weights=[0.65, 0.35])

    async def query_hybrid_async(self, dense_vec: List[float], query_text: str,
                                 tenant_id: str = "default", kb_ids: List[str] | None = None, limit: int = 10,
                                 acl_tags: List[str] | None = None, active_version_id: str | None = None,
                                 active_version_ids: List[str] | None = None):
        """async版hybrid: dense/sparse并发查询，共用召回前过滤条件."""
        query_filter = self._build_filter(
            tenant_id, kb_ids, acl_tags, active_version_id, active_version_ids)
        dense_res, sparse_res = await asyncio.gather(
            asyncio.to_thread(self.client.query_points, self.collection_name, query=dense_vec, using="dense",
                              query_filter=query_filter, limit=limit * 3, with_payload=True),
            asyncio.to_thread(self.client.query_points, self.collection_name,
                              query=Document(text=query_text, model="Qdrant/bm25"), using="sparse",
                              query_filter=query_filter, limit=limit * 3, with_payload=True),
        )
        return reciprocal_rank_fusion(responses=[dense_res.points, sparse_res.points],
                                       limit=limit, weights=[0.65, 0.35])

    async def delete_by_kb(self, kb_id: str):
        # ponytail: 按kb_id删points(删KB兜底+检索隔离), 幂等(空集不报错)
        self.client.delete(self.collection_name,
            points_selector=Filter(must=[FieldCondition(key="kb_id", match=MatchValue(value=kb_id))]))

    async def delete_by_version(self, version_id: str):
        # ponytail: 按version_id删points(删单文档; 历史payload document_id曾误写version_id, 按version最稳)
        self.client.delete(self.collection_name,
            points_selector=Filter(must=[FieldCondition(key="version_id", match=MatchValue(value=version_id))]))

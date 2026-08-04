# 检索层验证: 入库有区分度数据 -> dense / sparse-BM25 / RRF 三路对比 -> rerank
# 聚焦检索算法; HybridRetriever.retrieve(OSS拉文本)端到端在 test_agent_e2e 验证

import asyncio, os, sys, uuid
from dotenv import load_dotenv

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()

from qdrant_client import QdrantClient, models
from src.storage.qdrant_store import QdrantWriter, _normalize_host
from src.retrieval.embedder import embed_query
from src.retrieval.reranker import rerank
from src.storage.protocol import RetrievedDocument

# 2条相关 + 3条无关, 验证召回区分度
DOCS = [
    ("doc1", "DeepRAG 使用 Qdrant 向量数据库进行混合检索,结合密集向量与稀疏向量,并用RRF融合。"),
    ("doc2", "BGE-M3 是一个多语言嵌入模型,同时支持 dense 和 sparse 两种向量表示。"),
    ("doc3", "今天北京天气晴朗,气温25度,适合户外活动。"),
    ("doc4", "意大利面是经典的西餐主食,搭配番茄酱非常美味。"),
    ("doc5", "马拉松是一项长跑运动,需要良好的心肺耐力和长期训练。"),
]
QUERY = "DeepRAG 用什么向量数据库做检索"


def _show(title, points):
    print(f"\n  {title}:")
    for p in points[:5]:
        print(f"    {p.payload['document_id']}  score={p.score:.4f}")


async def main():
    print("=" * 60)
    print("DeepRAG 检索层验证: dense / sparse-BM25 / RRF / rerank")
    print("=" * 60)

    # 1. 重建 + 入库(payload带content便于rerank, 生产content在OSS)
    writer = QdrantWriter(host=os.getenv("QDRANT_HOST",""), port=int(os.getenv("QDRANT_PORT","6333")), api_key=os.getenv("QDRANT_API_KEY",""))
    writer.recreate_collection()
    chunks = []
    for doc_id, content in DOCS:
        dense = await embed_query(content)
        chunks.append({
            "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, doc_id)),
            "dense_vector": dense,
            "sparse_text": content,
            "payload": {"tenant_id": "default", "document_id": doc_id, "content": content},
        })
    await writer.insert_chunks(chunks)
    print(f"[1] 重建collection并入库 {len(chunks)} 条 (2相关 + 3无关)")

    host = _normalize_host(os.getenv("QDRANT_HOST",""))
    client = QdrantClient(url=f"{host}:{os.getenv('QDRANT_PORT')}", api_key=os.getenv("QDRANT_API_KEY") or None)
    q_dense = await embed_query(QUERY)

    # 2. 三路对比
    print(f"\n[2] 三路检索对比 (query: {QUERY})")
    r_dense = client.query_points("chunks", query=q_dense, using="dense", limit=5, with_payload=True)
    _show("dense-only (BGE-M3)", r_dense.points)
    r_sparse = client.query_points("chunks", query=models.Document(text=QUERY, model="Qdrant/bm25"), using="sparse", limit=5, with_payload=True)
    _show("sparse-only (BM25)", r_sparse.points)
    r_hybrid = client.query_points("chunks",
        prefetch=[
            models.Prefetch(query=q_dense, using="dense", limit=10),
            models.Prefetch(query=models.Document(text=QUERY, model="Qdrant/bm25"), using="sparse", limit=10),
        ],
        query=models.FusionQuery(fusion=models.Fusion.RRF), limit=5, with_payload=True)
    _show("hybrid (RRF)", r_hybrid.points)

    # 3. rerank 精排 hybrid 结果
    docs = [RetrievedDocument(chunk_id=str(p.id), document_id=p.payload["document_id"], version_id="",
        parent_id=None, content=p.payload["content"], locator={}, score=p.score, retrieval_source="hybrid_rrf")
        for p in r_hybrid.points]
    ranked = await rerank(QUERY, docs, top_n=5)
    print(f"\n[3] rerank 精排后:")
    for d in ranked:
        print(f"    {d.document_id}  score={d.score:.4f}  src={d.retrieval_source}")

    # 4. 判定: doc1/doc2 应在 hybrid top-2
    top2 = [d.document_id for d in ranked[:2]]
    relevant = sum(1 for d in top2 if d in ("doc1", "doc2"))
    print(f"\n[4] 相关文档(doc1/doc2)在 rerank top-2: {relevant}/2  -> {top2}")

    # RRF 是否优于单路: hybrid top-1 应是相关文档
    hybrid_top1 = r_hybrid.points[0].payload["document_id"]
    rrf_ok = hybrid_top1 in ("doc1", "doc2")
    print(f"    hybrid top-1: {hybrid_top1} ({'相关 ✓' if rrf_ok else '无关 ✗'})")

    ok = relevant >= 1 and rrf_ok
    print(f"\n{'✓ 检索层验证通过' if ok else '✗ 检索质量不达标,需检查'}")
    return ok


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)

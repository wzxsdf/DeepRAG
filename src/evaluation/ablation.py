# ponytail: 消融评估 - dense / +hybrid(加权RRF) / +rerank 检索指标, +CRAG 加生成指标

import os
import time
import math
import uuid
from dotenv import load_dotenv

from qdrant_client import QdrantClient
from src.storage.qdrant_store import QdrantWriter, _normalize_host
from src.retrieval.embedder import embed_query
from src.retrieval.reranker import rerank
from src.storage.protocol import RetrievedDocument
from src.retrieval.hybrid import HybridRetriever
from src.agents.graph import build_graph
from src.evaluation.golden import DOCUMENTS, GOLDEN

load_dotenv()


def _client() -> QdrantClient:
    host = _normalize_host(os.getenv("QDRANT_HOST", ""))
    return QdrantClient(url=f"{host}:{os.getenv('QDRANT_PORT')}", api_key=os.getenv("QDRANT_API_KEY") or None)


def _writer() -> QdrantWriter:
    return QdrantWriter(host=os.getenv("QDRANT_HOST", ""), port=int(os.getenv("QDRANT_PORT", "6333")),
                        api_key=os.getenv("QDRANT_API_KEY", ""))


async def ingest_eval_set() -> int:
    """recreate + 入库评估文档集(sparse_text用jieba分词, payload含content供CRAG fallback)."""
    writer = _writer()
    writer.recreate_collection()
    chunks = []
    for doc_id, content in DOCUMENTS:
        dense = await embed_query(content)
        chunks.append({"id": str(uuid.uuid5(uuid.NAMESPACE_DNS, doc_id)),
                       "dense_vector": dense, "sparse_text": content,
                       "payload": {"tenant_id": "default", "document_id": doc_id, "content": content}})
    await writer.insert_chunks(chunks)
    return len(chunks)


def _metrics(top_ids, relevant: set, k: int = 5) -> dict:
    """返回 Recall@1/5, MRR, nDCG@5 (二元相关性)."""
    rel = [1 if d in relevant else 0 for d in top_ids[:k]]
    recall5 = sum(rel) / len(relevant) if relevant else 0.0
    recall1 = 1.0 if (rel and rel[0]) else 0.0
    mrr = 0.0
    for i, d in enumerate(top_ids):
        if d in relevant:
            mrr = 1.0 / (i + 1)
            break
    # ponytail: nDCG@k 二元相关性, DCG=sum(rel_i/log2(i+2)); IDCG=理想排序(rel全前置)
    dcg = sum(rel[i] / math.log2(i + 2) for i in range(len(rel)))
    idcg = sum(1.0 / math.log2(i + 2) for i in range(min(len(relevant), k)))
    ndcg = dcg / idcg if idcg > 0 else 0.0
    return {"recall@1": recall1, "recall@5": recall5, "mrr": mrr, "ndcg@5": ndcg}


def _pct(sorted_vals: list[float], pct: float) -> float:
    """百分位(p50/p95). 小样本N=10用最近秩, 够用."""
    if not sorted_vals:
        return 0.0
    idx = max(0, min(len(sorted_vals) - 1, round(pct / 100 * (len(sorted_vals) - 1))))
    return sorted_vals[int(idx)]


async def eval_retrieval(config: str) -> dict:
    """config: dense / hybrid / hybrid_rerank. 返回检索指标 + 单次检索延迟(p50/p95)."""
    client = _client()
    writer = _writer()
    agg = {"recall@1": 0.0, "recall@5": 0.0, "mrr": 0.0, "ndcg@5": 0.0}
    lats: list[float] = []
    for g in GOLDEN:
        q_dense = await embed_query(g["q"])
        relevant = set(g["relevant"])
        t0 = time.perf_counter()
        if config == "dense":
            r = client.query_points("chunks", query=q_dense, using="dense", limit=5, with_payload=True)
            top_ids = [p.payload["document_id"] for p in r.points]
        else:
            fused = writer.query_hybrid(q_dense, g["q"], tenant_id="default", limit=5)
            if config == "hybrid":
                top_ids = [p.payload["document_id"] for p in fused]
            else:  # hybrid_rerank
                docs = [RetrievedDocument(chunk_id=str(p.id), document_id=p.payload["document_id"], version_id="",
                         parent_id=None, content=p.payload["content"], locator={}, score=p.score, retrieval_source="hybrid_rrf")
                        for p in fused]
                ranked = await rerank(g["q"], docs, top_n=5)
                top_ids = [d.document_id for d in ranked]
        lats.append((time.perf_counter() - t0) * 1000)
        for kk, vv in _metrics(top_ids, relevant).items():
            agg[kk] += vv
    n = len(GOLDEN)
    s = sorted(lats)
    return {kk: agg[kk] / n for kk in agg} | {"p50_ms": _pct(s, 50), "p95_ms": _pct(s, 95), "n": n}


async def eval_crag(subset: int | None = None) -> dict:
    """跑CRAG状态机, 检索指标(基于最终documents) + 生成指标(verify自评) + 端到端延迟."""
    golden = GOLDEN[:subset] if subset else GOLDEN
    graph = build_graph(HybridRetriever())
    agg = {"recall@1": 0.0, "recall@5": 0.0, "mrr": 0.0, "ndcg@5": 0.0}
    faith = cite = 0.0
    lats: list[float] = []
    for g in golden:
        cfg = {"configurable": {"thread_id": f"crag-{g['q'][:8]}"}}
        t0 = time.perf_counter()
        await graph.ainvoke(
            {"question": g["q"], "query": g["q"], "tenant_id": "default", "retry_count": 0,
             "documents": [], "search_decision": "", "generation": "", "verification": None}, config=cfg)
        lats.append((time.perf_counter() - t0) * 1000)
        values = (await graph.aget_state(config=cfg)).values
        top_ids = [d.document_id for d in values.get("documents", [])]
        for kk, vv in _metrics(top_ids, set(g["relevant"])).items():
            agg[kk] += vv
        ver = values.get("verification") or {}
        faith += 1.0 if ver.get("grounded") else 0.0
        cite += 1.0 if ver.get("citations_valid") else 0.0
    n = len(golden)
    s = sorted(lats)
    return {kk: agg[kk] / n for kk in agg} | {
        "faithfulness": faith / n, "citation_acc": cite / n,
        "p50_ms": _pct(s, 50), "p95_ms": _pct(s, 95), "n": n}

# ponytail: 检索漏斗消融评估, 使用每次运行独占的Qdrant集合

import math
import os
import time
import uuid
from typing import Any

from dotenv import load_dotenv

from src.agents.graph import build_graph
from src.core.logging import log
from src.evaluation.golden import DOCUMENTS, GOLDEN, UNANSWERABLE
from src.retrieval.embedder import embed_query
from src.retrieval.hybrid import HybridRetriever
from src.retrieval.reranker import deduplicate_documents, rerank
from src.storage.protocol import RetrievedDocument
from src.storage.qdrant_store import QdrantWriter

load_dotenv()


def create_eval_writer() -> QdrantWriter:
    """Create a fresh, uniquely named collection; never reuse or recreate a shared collection."""
    collection_name = f"eval_{uuid.uuid4().hex}"
    writer = QdrantWriter(
        host=os.getenv("QDRANT_HOST", ""),
        port=int(os.getenv("QDRANT_PORT", "6333")),
        api_key=os.getenv("QDRANT_API_KEY", ""),
        collection_name=collection_name,
    )
    if writer.client.collection_exists(collection_name):
        raise RuntimeError(f"Refusing to reuse existing evaluation collection: {collection_name}")
    writer.ensure_collection()
    return writer


def delete_eval_collection(writer: QdrantWriter) -> None:
    """Delete only the unique evaluation collection allocated by this run.
    ponytail: 清理失败仅告警不抛(20261126实测: Qdrant代理偶发502会让finally掩盖try块根因;
    孤儿集合可按eval_前缀巡检清理, 前缀守卫仍保留——非eval_集合拒绝删除)."""
    if not writer.collection_name.startswith("eval_"):
        raise ValueError("Refusing to delete a non-evaluation collection")
    try:
        if writer.client.collection_exists(writer.collection_name):
            writer.client.delete_collection(writer.collection_name)
    except Exception as e:
        log.warning("eval_collection_cleanup_failed", collection=writer.collection_name,
                    error=type(e).__name__, msg=str(e))


async def ingest_eval_set(writer: QdrantWriter) -> int:
    """Insert the deterministic golden documents into a fresh isolated collection."""
    chunks = []
    for doc_id, content in DOCUMENTS:
        dense = await embed_query(content)
        chunks.append({
            "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, doc_id)),
            "dense_vector": dense,
            "sparse_text": content,
            "payload": {"tenant_id": "default", "document_id": doc_id, "content": content},
        })
    await writer.insert_chunks(chunks)
    return len(chunks)


def _metrics(top_ids: list[str], relevant: set[str], k: int = 5) -> dict[str, float]:
    """Calculate binary document-level recall, MRR@k, and nDCG@k."""
    unique_ids = list(dict.fromkeys(top_ids))
    ranked = unique_ids[:k]
    rel = [1 if doc_id in relevant else 0 for doc_id in ranked]
    recall = len(set(ranked) & relevant) / len(relevant) if relevant else 0.0
    reciprocal_rank = next((1.0 / i for i, is_relevant in enumerate(rel, 1) if is_relevant), 0.0)
    dcg = sum(is_relevant / math.log2(i + 1) for i, is_relevant in enumerate(rel, 1))
    ideal_count = min(len(relevant), k)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_count + 1))
    return {
        "recall@1": float(bool(rel and rel[0])),
        "recall@5": len(set(ranked[:5]) & relevant) / len(relevant) if relevant else 0.0,
        "mrr@5": reciprocal_rank,
        "ndcg@5": dcg / idcg if idcg else 0.0,
        "recall@candidate": recall,
    }


def _pct(sorted_vals: list[float], pct: float) -> float:
    """Nearest-rank percentile, suitable for reporting the small smoke set."""
    if not sorted_vals:
        return 0.0
    idx = max(0, min(len(sorted_vals) - 1, math.ceil(pct / 100 * len(sorted_vals)) - 1))
    return sorted_vals[idx]


def _to_documents(points: list[Any]) -> list[RetrievedDocument]:
    docs = []
    for point in points:
        payload = point.payload or {}
        docs.append(RetrievedDocument(
            chunk_id=str(point.id),
            document_id=payload.get("document_id", ""),
            version_id=payload.get("version_id", ""),
            parent_id=payload.get("parent_id"),
            content=payload.get("content", ""),
            locator={},
            score=point.score,
            retrieval_source="hybrid_rrf",
        ))
    return docs


async def eval_retrieval(config: str, writer: QdrantWriter) -> dict[str, float | int | None]:
    """Compare dense/hybrid baselines and top-k → dedup → rerank funnels."""
    configs = {
        "dense": {"kind": "dense", "top_k": 5},
        "hybrid": {"kind": "hybrid", "top_k": 5},
        "funnel_top5_rerank5": {"kind": "rerank", "top_k": 5},
        "funnel_top20_rerank5": {"kind": "rerank", "top_k": 20},
    }
    if config not in configs:
        raise ValueError(f"Unknown retrieval evaluation config: {config}")
    spec = configs[config]
    agg = {"candidate_recall@5": 0.0, "candidate_recall@20": 0.0,
           "recall@1": 0.0, "recall@5": 0.0, "mrr@5": 0.0, "ndcg@5": 0.0}
    candidate_20_count = 0
    fallback_count = 0
    lats: list[float] = []

    for golden in GOLDEN:
        started = time.perf_counter()
        dense = await embed_query(golden["q"])
        if spec["kind"] == "dense":
            response = writer.client.query_points(
                writer.collection_name, query=dense, using="dense", limit=spec["top_k"],
                with_payload=True,
            )
            candidates = _to_documents(response.points)
        else:
            points = writer.query_hybrid(
                dense, golden["q"], tenant_id="default", limit=spec["top_k"])
            candidates = deduplicate_documents(_to_documents(points))

        candidate_ids = [doc.document_id for doc in candidates]
        relevant = set(golden["relevant"])
        candidate5 = _metrics(candidate_ids[:5], relevant, k=5)["recall@candidate"]
        agg["candidate_recall@5"] += candidate5
        if len(candidate_ids) >= 20 or spec["top_k"] == 20:
            candidate20 = _metrics(candidate_ids[:20], relevant, k=20)["recall@candidate"]
            agg["candidate_recall@20"] += candidate20
            candidate_20_count += 1

        final_docs = candidates
        if spec["kind"] == "rerank":
            final_docs = await rerank(golden["q"], candidates, top_n=5)
            if final_docs and any(doc.retrieval_source != "rerank" for doc in final_docs):
                fallback_count += 1
        final_metrics = _metrics([doc.document_id for doc in final_docs], relevant, k=5)
        for key in ("recall@1", "recall@5", "mrr@5", "ndcg@5"):
            agg[key] += final_metrics[key]
        lats.append((time.perf_counter() - started) * 1000)

    n = len(GOLDEN)
    return {
        **{key: value / n for key, value in agg.items() if key != "candidate_recall@20"},
        "candidate_recall@20": agg["candidate_recall@20"] / candidate_20_count
        if candidate_20_count else None,
        "p50_ms": _pct(sorted(lats), 50),
        "p95_ms": _pct(sorted(lats), 95),
        "rerank_fallbacks": fallback_count,
        "n": n,
    }


async def _judge_answer(question: str, answer: str, docs: list, reference: str) -> dict | None:
    """P1② 独立judge: 异模型(purpose=judge→CHAT_JUDGE_MODEL)对照参考答案评分, 替代verifier自评(1.000无信息量).
    失败/解析错返回None(计skip, 不进judge均值). CHAT_JUDGE_MODEL空时purpose回退fast=自评口径, 报告应标注."""
    try:
        from src.core.llm import chat_json
        from src.prompts import JUDGE_PROMPT
        evidence = "\n\n".join(f"[{i+1}] {d.content}" for i, d in enumerate(docs))
        res = await chat_json([{"role": "user", "content": JUDGE_PROMPT.format(
            question=question, reference=reference, answer=answer, evidence=evidence)}],
            purpose="judge")
        if not isinstance(res, dict) or "error" in res:
            return None
        return res
    except Exception as e:
        log.warning("judge_call_failed", q=question[:24], error=type(e).__name__, msg=str(e))
        return None


async def eval_crag(writer: QdrantWriter, subset: int | None = None) -> dict[str, Any]:
    """Run CRAG with the isolated collection; per-question retries captured for p95 tail attribution.
    P1②: 带参考答案的问题额外走独立judge(异模型)评answer_correct/grounded/citation."""
    golden = GOLDEN[:subset] if subset else GOLDEN
    retriever = HybridRetriever(qdrant=writer, resolve_active_versions=False)
    graph = build_graph(retriever)
    agg = {"recall@1": 0.0, "recall@5": 0.0, "mrr@5": 0.0, "ndcg@5": 0.0}
    faith = cite = 0.0
    judge_answer = judge_grounded = judge_citation = 0.0
    judge_n = 0
    lats: list[float] = []
    per_question: list[dict[str, Any]] = []
    for item in golden:
        cfg = {"configurable": {"thread_id": f"crag-{uuid.uuid4().hex}"}}
        started = time.perf_counter()
        # ponytail: checkpointer已移除(评审P2) — ainvoke直接返回最终state, 不再aget_state
        values = await graph.ainvoke(
            {"question": item["q"], "query": item["q"], "tenant_id": "default", "retry_count": 0,
             "documents": [], "search_decision": "", "generation": "", "verification": None,
             "mode": "kb"},  # ponytail: 显式kb模式走CRAG检索闭环; 缺省quick会进tool_agent(联网), 与检索消融不可比
            config=cfg,
        )
        lat_ms = (time.perf_counter() - started) * 1000
        lats.append(lat_ms)
        metrics = _metrics([doc.document_id for doc in values.get("documents", [])],
                           set(item["relevant"]))
        for key in agg:
            agg[key] += metrics[key]
        verification = values.get("verification") or {}
        faith += float(bool(verification.get("grounded")))
        cite += float(bool(verification.get("citations_valid")))
        # P1②: 独立judge对照参考答案评分(带answer字段的问题才评)
        ref = item.get("answer")
        if ref:
            j = await _judge_answer(item["q"], values.get("generation", ""),
                                    values.get("documents", []), ref)
            if j is not None:
                judge_n += 1
                judge_answer += float(bool(j.get("answer_correct")))
                judge_grounded += float(bool(j.get("grounded")))
                judge_citation += float(bool(j.get("citation_correct")))
        retries = int(values.get("retry_count") or 0)
        per_question.append({"q": item["q"], "lat_ms": lat_ms, "retries": retries,
                             "recall@5": metrics["recall@5"],
                             "grounded": bool(verification.get("grounded"))})
    n = len(golden)
    p95 = _pct(sorted(lats), 95)
    # ponytail: p95长尾归因 - 超过p95阈值的问题逐条列出(重试次数/召回/grounded), 供判定长尾是否为重试共性问题
    tail = sorted((r for r in per_question if r["lat_ms"] >= p95), key=lambda r: -r["lat_ms"])
    retried_n = sum(1 for r in per_question if r["retries"] > 0)
    return {key: value / n for key, value in agg.items()} | {
        "faithfulness": faith / n,
        "citation_acc": cite / n,
        "judge_answer": judge_answer / judge_n if judge_n else None,
        "judge_grounded": judge_grounded / judge_n if judge_n else None,
        "judge_citation": judge_citation / judge_n if judge_n else None,
        "judge_n": judge_n,
        "p50_ms": _pct(sorted(lats), 50),
        "p95_ms": p95,
        "retries_total": sum(r["retries"] for r in per_question),
        "retried_n": retried_n,
        "tail": tail,
        "n": n,
    }


async def eval_crag_unanswerable(writer: QdrantWriter) -> dict[str, Any]:
    """P1② 不可答负样本(UNANSWERABLE, 语料无答案): 期望 grade 判全部无关→降级 tool_agent 兜底.
    诚实降级(objective): citations为空 且 source != kb_grounded — 编造kb_grounded引用即计不诚实."""
    retriever = HybridRetriever(qdrant=writer, resolve_active_versions=False)
    graph = build_graph(retriever)
    lats: list[float] = []
    per_q: list[dict[str, Any]] = []
    for q in UNANSWERABLE:
        cfg = {"configurable": {"thread_id": f"una-{uuid.uuid4().hex}"}}
        started = time.perf_counter()
        values = await graph.ainvoke(
            {"question": q, "query": q, "tenant_id": "default", "retry_count": 0,
             "documents": [], "search_decision": "", "generation": "", "verification": None,
             "mode": "kb"}, config=cfg)
        lat_ms = (time.perf_counter() - started) * 1000
        lats.append(lat_ms)
        docs = values.get("documents", [])
        source = values.get("source", "")
        honest = (len(docs) == 0 and source != "kb_grounded")
        per_q.append({"q": q, "lat_ms": lat_ms, "source": source,
                      "citations": len(docs), "honest": honest})
    n = len(UNANSWERABLE)
    honest_n = sum(1 for r in per_q if r["honest"])
    sources: dict[str, int] = {}
    for r in per_q:
        sources[r["source"]] = sources.get(r["source"], 0) + 1
    return {
        "n": n,
        "honest_rate": honest_n / n if n else None,
        "honest_n": honest_n,
        "sources": sources,
        "p50_ms": _pct(sorted(lats), 50),
        "p95_ms": _pct(sorted(lats), 95),
        "per_question": per_q,
    }

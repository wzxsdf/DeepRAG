# ponytail: 检索漏斗消融评估, 使用每次运行独占的Qdrant集合
# CRAG评测支持断点续跑(ckpt jsonl + max_questions分块): 宿主/环境会静默杀死~20min的后台长跑
# (20261126实测两次, 日志冻结无Traceback), 分块+续跑使任何一次死亡只损失当前块

import json
import asyncio
import math
import os
import time
import uuid
from pathlib import Path
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

    # v5: 多轮问(带history)跳过漏斗 — follow-up单独检索必失配, 漏斗保持单轮口径; 多轮质量由CRAG行承载
    single_turn = [g for g in GOLDEN if not g.get("history")]
    for golden in single_turn:
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

    n = len(single_turn)
    return {
        **{key: value / n for key, value in agg.items() if key != "candidate_recall@20"},
        "candidate_recall@20": agg["candidate_recall@20"] / candidate_20_count
        if candidate_20_count else None,
        "p50_ms": _pct(sorted(lats), 50),
        "p95_ms": _pct(sorted(lats), 95),
        "rerank_fallbacks": fallback_count,
        "n": n,
    }


async def _judge_answer(question: str, answer: str, docs: list, reference: str,
                        history: list | None = None) -> dict | None:
    """P1② 独立judge: 异模型(purpose=judge→CHAT_JUDGE_MODEL)对照参考答案评分, 替代verifier自评(1.000无信息量).
    多轮问(golden v5)携带history消解指代 — judge与被评系统看同样上下文, 否则follow-up必评错.
    失败/解析错返回None(计skip, 不进judge均值). CHAT_JUDGE_MODEL空时purpose回退fast=自评口径, 报告应标注."""
    try:
        from src.core.llm import chat_json
        from src.prompts import JUDGE_PROMPT
        evidence = "\n\n".join(f"[{i+1}] {d.content}" for i, d in enumerate(docs))
        if history:
            hist_text = "\n".join(f"用户: {h.get('question', '')}\n助手: {h.get('answer', '')}"
                                  for h in history)
            question = f"对话历史(用于消解指代):\n{hist_text}\n\n当前问题: {question}"
        res = await chat_json([{"role": "user", "content": JUDGE_PROMPT.format(
            question=question, reference=reference, answer=answer, evidence=evidence)}],
            purpose="judge")
        if not isinstance(res, dict) or "error" in res:
            return None
        return res
    except Exception as e:
        log.warning("judge_call_failed", q=question[:24], error=type(e).__name__, msg=str(e))
        return None


def _load_ckpt_records(ckpt_path: str | None) -> list[dict[str, Any]]:
    if not ckpt_path:
        return []
    p = Path(ckpt_path)
    if not p.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except Exception:
            continue
    # ponytail: 并发分块可能双写同一idx(20261126实测), 聚合按idx去重(保留最后一次写入)
    by_idx: dict[Any, dict[str, Any]] = {}
    for r in records:
        if isinstance(r.get("idx"), int):
            by_idx[r["idx"]] = r
    return [by_idx[k] for k in sorted(by_idx)]


def _ckpt_append(ckpt_path: str | None, rec: dict[str, Any]) -> None:
    if not ckpt_path:
        return
    with open(ckpt_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


async def eval_crag(writer: QdrantWriter, subset: int | None = None,
                    ckpt_path: str | None = None,
                    max_questions: int | None = None,
                    idx_from: int | None = None, idx_to: int | None = None) -> dict[str, Any]:
    """Run CRAG with the isolated collection; per-question retries captured for p95 tail attribution.
    P1②: 带参考答案的问题额外走独立judge(异模型)评answer_correct/grounded/citation.
    golden v5: 多轮问(item["history"])注入conversation_history — 预改写指代消解/多轮grade链路被端到端评测.
    断点续跑: ckpt_path载入已完成逐问记录, max_questions限制单次块大小(20261126实测后台长跑
    会在~21分钟被静默杀死且无Traceback, 分块使损失有界); 未跑完返回{"partial": True}由调用方续跑.
    idx_from/idx_to: 只跑[idx_from, idx_to)区间(v5多轮问单独跑用, 免重跑前100单轮)."""
    golden = GOLDEN[:subset] if subset else GOLDEN
    records = [r for r in _load_ckpt_records(ckpt_path)
               if isinstance(r.get("idx"), int) and 0 <= r["idx"] < len(golden)]
    done_idx = {r["idx"] for r in records}
    pending = [i for i in range(len(golden)) if i not in done_idx]
    if idx_from is not None or idx_to is not None:
        lo = idx_from if idx_from is not None else 0
        hi = idx_to if idx_to is not None else len(golden)
        pending = [i for i in pending if lo <= i < hi]
    if max_questions is not None:
        pending = pending[:max_questions]

    retriever = HybridRetriever(qdrant=writer, resolve_active_versions=False)
    graph = build_graph(retriever)
    suspect_n = 0  # 可疑签名: recall@1=0 + 无grounded + 0重试(=检索分数坏/端点退化窗口)
    QUESTION_TIMEOUT_S = 600  # 单问硬顶(20261127实测: 无timeout时端点黑洞可挂13min+, CPU栈=事件循环空等IOCP)
    timeout_n = 0
    for idx in pending:
        item = golden[idx]
        cfg = {"configurable": {"thread_id": f"crag-{uuid.uuid4().hex}"}}
        started = time.perf_counter()
        # ponytail: checkpointer已移除(评审P2) — ainvoke直接返回最终state, 不再aget_state
        try:
            values = await asyncio.wait_for(
                graph.ainvoke(
                    {"question": item["q"], "query": item["q"], "tenant_id": "default", "retry_count": 0,
                     "documents": [], "search_decision": "", "generation": "", "verification": None,
                     "conversation_history": item.get("history") or [],  # v5: 多轮问注入历史(预改写指代消解依赖它)
                     "mode": "kb"},  # ponytail: 显式kb模式走CRAG检索闭环; 缺省quick会进tool_agent(联网), 与检索消融不可比
                    config=cfg,
                ), timeout=QUESTION_TIMEOUT_S)
        except asyncio.TimeoutError:
            # 挂死→记可疑超时记录继续跑(不丢进度); 超时签名自然满足可疑判定(0分+无grounded+0重试)→坏窗口熔断会接手
            timeout_n += 1
            log.warning("eval_crag_question_timeout", idx=idx, q=item["q"][:40], timeout_s=QUESTION_TIMEOUT_S)
            _ckpt_append(ckpt_path, {"idx": idx, "q": item["q"], "lat_ms": QUESTION_TIMEOUT_S * 1000,
                                     "recall@1": 0.0, "recall@5": 0.0, "mrr@5": 0.0, "ndcg@5": 0.0,
                                     "grounded": False, "citations_valid": False, "retries": 0,
                                     "judge_answer": None, "judge_grounded": None,
                                     "judge_citation": None, "timeout": True})
            records.append({"idx": idx, "q": item["q"], "lat_ms": QUESTION_TIMEOUT_S * 1000,
                            "recall@1": 0.0, "recall@5": 0.0, "mrr@5": 0.0, "ndcg@5": 0.0,
                            "grounded": False, "citations_valid": False, "retries": 0,
                            "judge_answer": None, "judge_grounded": None, "judge_citation": None,
                            "timeout": True})
            suspect_n += 1
            continue
        lat_ms = (time.perf_counter() - started) * 1000
        metrics = _metrics([doc.document_id for doc in values.get("documents", [])],
                           set(item["relevant"]))
        verification = values.get("verification") or {}
        retries = int(values.get("retry_count") or 0)
        done_in_run = idx - pending[0] + 1
        if metrics["recall@1"] == 0.0 and not verification.get("grounded") and retries == 0:
            suspect_n += 1
            # 坏窗口熔断(20261127凌晨实测: SiliconFlow夜间退化窗口, 12/13问全零分, 重试无法自愈):
            # 已测≥8问且可疑率≥75% → 判定坏窗口, 立即中止(ckpt保留已完成问, 换窗口续跑)
            if done_in_run >= 8 and suspect_n / done_in_run >= 0.75:
                _ckpt_append(ckpt_path, {"idx": idx, "q": item["q"], "lat_ms": lat_ms,
                                         "recall@1": metrics["recall@1"], "recall@5": metrics["recall@5"],
                                         "mrr@5": metrics["mrr@5"], "ndcg@5": metrics["ndcg@5"],
                                         "grounded": False, "citations_valid": False, "retries": retries,
                                         "judge_answer": None, "judge_grounded": None,
                                         "judge_citation": None, "suspect": True})
                raise RuntimeError(
                    f"eval_crag坏窗口熔断: 前{done_in_run}问中{suspect_n}问可疑(recall@1=0且无grounded且0重试), "
                    "疑似LLM/rerank/embedding端点退化窗口; ckpt已保留已完成问题, 请换时间窗口续跑")
        rec: dict[str, Any] = {
            "idx": idx, "q": item["q"], "lat_ms": lat_ms,
            "recall@1": metrics["recall@1"], "recall@5": metrics["recall@5"],
            "mrr@5": metrics["mrr@5"], "ndcg@5": metrics["ndcg@5"],
            "grounded": bool(verification.get("grounded")),
            "citations_valid": bool(verification.get("citations_valid")),
            "retries": retries,
            "judge_answer": None, "judge_grounded": None, "judge_citation": None,
            "mt": bool(item.get("history")),  # v5: 多轮问标记(聚合分析用)
        }
        # P1②: 独立judge对照参考答案评分(带answer字段的问题才评); v5多轮问judge携带历史消解指代
        ref = item.get("answer")
        if ref:
            try:
                j = await asyncio.wait_for(
                    _judge_answer(item["q"], values.get("generation", ""),
                                  values.get("documents", []), ref,
                                  history=item.get("history")), timeout=180)
            except asyncio.TimeoutError:
                log.warning("eval_crag_judge_timeout", idx=idx)
                j = None
            if j is not None:
                rec["judge_answer"] = bool(j.get("answer_correct"))
                rec["judge_grounded"] = bool(j.get("grounded"))
                rec["judge_citation"] = bool(j.get("citation_correct"))
        records.append(rec)
        _ckpt_append(ckpt_path, rec)
        log.info("eval_crag_progress", idx=idx, done=len(records), total=len(golden))

    lo = idx_from if idx_from is not None else 0
    hi = idx_to if idx_to is not None else len(golden)
    targets = [i for i in range(len(golden)) if lo <= i < hi]
    done = {r["idx"] for r in records}
    if any(i not in done for i in targets):
        return {"partial": True, "done_n": len(records), "n": len(targets)}
    target_records = [r for r in records if r["idx"] in set(targets)]
    return _aggregate_crag(target_records, len(targets))


def _aggregate_crag(records: list[dict[str, Any]], n: int) -> dict[str, Any]:
    """从逐问记录聚合CRAG指标(断点续跑与一次性跑共用同一口径)."""
    agg = {"recall@1": 0.0, "recall@5": 0.0, "mrr@5": 0.0, "ndcg@5": 0.0}
    faith = cite = 0.0
    judge_answer = judge_grounded = judge_citation = 0.0
    judge_n = 0
    for r in records:
        for key in agg:
            agg[key] += r[key]
        faith += float(r["grounded"])
        cite += float(r["citations_valid"])
        if r.get("judge_answer") is not None:
            judge_n += 1
            judge_answer += float(bool(r["judge_answer"]))
            judge_grounded += float(bool(r["judge_grounded"]))
            judge_citation += float(bool(r["judge_citation"]))
    lats = sorted(r["lat_ms"] for r in records)
    p95 = _pct(lats, 95)
    per_question = [{"q": r["q"], "lat_ms": r["lat_ms"], "retries": r["retries"],
                     "recall@5": r["recall@5"], "grounded": r["grounded"]} for r in records]
    tail = sorted((r for r in per_question if r["lat_ms"] >= p95), key=lambda r: -r["lat_ms"])
    retried_n = sum(1 for r in per_question if r["retries"] > 0)
    return {key: value / n for key, value in agg.items()} | {
        "faithfulness": faith / n,
        "citation_acc": cite / n,
        "judge_answer": judge_answer / judge_n if judge_n else None,
        "judge_grounded": judge_grounded / judge_n if judge_n else None,
        "judge_citation": judge_citation / judge_n if judge_n else None,
        "judge_n": judge_n,
        "p50_ms": _pct(lats, 50),
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

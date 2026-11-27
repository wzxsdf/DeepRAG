# rerank阈值标定探针(P0): 对指定rerank模型, 采样golden正问题+全部不可答负样本,
# 输出各组top1分数分布与正负分离最优阈值 — 换rerank模型必须先跑本脚本重标定
# (retrieval_min_score + rerank_selfheal_min 随模型分布变, 见config注释)
# 用法: RERANK_MODEL=Qwen/Qwen3-Reranker-0.6B python scripts/calibrate_rerank.py [--pos-n 30]
import argparse
import asyncio
import os
import random
import statistics
import sys
import time

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

from dotenv import load_dotenv

load_dotenv()
os.environ.setdefault("NO_PROXY", "*")

# RERANK_MODEL 必须在首次 get_settings() 之前就位(env→Settings), 本脚本不做进程内切换
from src.evaluation.ablation import create_eval_writer, delete_eval_collection, ingest_eval_set
from src.evaluation.golden import GOLDEN, UNANSWERABLE
from src.retrieval.hybrid import HybridRetriever
from src.retrieval.reranker import rerank
from src.storage.protocol import RetrievalFilter
from src.core.config import get_settings


async def main(pos_n: int):
    model = get_settings().rerank_model
    print(f"标定模型: {model} | 正样本{pos_n}问(分层抽样) + 负样本{len(UNANSWERABLE)}问(全量)")

    writer = create_eval_writer()
    try:
        await ingest_eval_set(writer)
        retriever = HybridRetriever(qdrant=writer, resolve_active_versions=False)

        random.seed(42)  # 确定性抽样: 跨模型可比
        positives = random.sample(GOLDEN, min(pos_n, len(GOLDEN)))
        pos_scores, neg_scores, lats = [], [], []
        for kind, qs, sink in (("pos", positives, pos_scores), ("neg", UNANSWERABLE, neg_scores)):
            for g in qs:
                q = g["q"] if kind == "pos" else g
                t0 = time.perf_counter()
                docs = await retriever.retrieve(q, RetrievalFilter(tenant_id="default"), top_k=20)
                ranked = await rerank(q, docs, top_n=5)
                lats.append((time.perf_counter() - t0) * 1000)
                top1 = max((d.score for d in ranked), default=0.0)
                sink.append(top1)
                print(f"  [{kind}] {q[:28]:<30} top1={top1:.4f}")

        def summary(name, xs):
            xs_sorted = sorted(xs)
            print(f"\n{name}: n={len(xs)} min={xs_sorted[0]:.4f} p25={xs_sorted[len(xs)//4]:.4f} "
                  f"median={statistics.median(xs_sorted):.4f} p75={xs_sorted[3*len(xs)//4]:.4f} max={xs_sorted[-1]:.4f}")
            return xs_sorted

        ps = summary("正样本(应答问) top1", pos_scores)
        ns = summary("负样本(不可答) top1", neg_scores)
        print(f"rerank延迟: p50={statistics.median(lats):.0f}ms max={max(lats):.0f}ms")

        # 最优分离阈值: 真阳率(正样本≥t)与真阴率(负样本<t)之和最大
        best_t, best_acc, best_detail = 0.0, -1.0, None
        for t in [round(x * 0.01, 2) for x in range(1, 100)]:
            tp = sum(1 for s in ps if s >= t) / len(ps)
            tn = sum(1 for s in ns if s < t) / len(ns)
            if tp + tn > best_acc:
                best_acc, best_t = tp + tn, t
                best_detail = (tp, tn)
        print(f"\n>>> 建议阈值: retrieval_min_score={best_t:.2f} "
              f"(正样本通过率={best_detail[0]:.2%}, 负样本拦截率={best_detail[1]:.2%}, 平衡={best_acc/2:.2%})")
        print(f">>> 自愈触发线(rerank_selfheal_min)建议=阈值同值 {best_t:.2f}")
    finally:
        delete_eval_collection(writer)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pos-n", type=int, default=30)
    a = ap.parse_args()
    asyncio.run(main(a.pos_n))

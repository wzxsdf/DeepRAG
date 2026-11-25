# W2消融矩阵: dense-only / +hybrid(RRF) / 漏斗对照(top5→rerank5 vs top20→rerank5) / +CRAG(agent)
# 每次运行创建独占 eval_<uuid> Qdrant集合, 结束后仅清理本次集合; 报告写入 docs/trials/<trial_id>_* 不覆盖历史基线
# 注意: 当前golden仅10问, 为smoke baseline, 数字不作为最终结论(样本量限制在报告中标注)

import asyncio
import csv
import os
import sys
from datetime import datetime

from dotenv import load_dotenv

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()

from src.evaluation.ablation import create_eval_writer, delete_eval_collection, eval_crag, eval_retrieval, ingest_eval_set
from src.evaluation.golden import GOLDEN

CONFIGS = [
    ("dense", "dense-only"),
    ("hybrid", "+hybrid(RRF)"),
    ("funnel_top5_rerank5", "funnel top5->rerank5"),
    ("funnel_top20_rerank5", "funnel top20->rerank5"),
]
COLS = ["candidate_recall@5", "candidate_recall@20", "recall@1", "recall@5", "mrr@5", "ndcg@5",
        "faithfulness", "citation_acc", "p50_ms", "p95_ms", "rerank_fallbacks"]


def _f(v, default="-"):
    """数值格式化, None→'-'; 整数型指标(fallback/n)不带小数."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return default
    return str(int(v)) if isinstance(v, int) else f"{v:.3f}"


def _ms(v):
    return f"{v:.0f}" if isinstance(v, (int, float)) else "-"


async def main():
    trial_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    print("=" * 104)
    print(f"DeepRAG 消融+漏斗对照 trial={trial_id} (Golden N={len(GOLDEN)}, smoke baseline)")
    print("=" * 104)

    writer = create_eval_writer()
    rows = []
    try:
        n_docs = await ingest_eval_set(writer)
        print(f"评测集合 {writer.collection_name}: 入库 {n_docs} chunks\n")

        for cfg, label in CONFIGS:
            m = await eval_retrieval(cfg, writer)
            rows.append({"config": label, **m, "faithfulness": None, "citation_acc": None})
            print(f"  {label:<20} candR@5={_f(m.get('candidate_recall@5'))} candR@20={_f(m.get('candidate_recall@20'))} "
                  f"R@1={m['recall@1']:.3f} R@5={m['recall@5']:.3f} MRR@5={m['mrr@5']:.3f} nDCG@5={m['ndcg@5']:.3f} "
                  f"p50={_ms(m['p50_ms'])}ms p95={_ms(m['p95_ms'])}ms fallback={m.get('rerank_fallbacks', '-')}")

        print("\n  +CRAG(agent工具检索, 含生成指标, 调LLM较慢)...")
        crag = await eval_crag(writer)
        rows.append({"config": "+CRAG", **crag, "candidate_recall@5": None, "candidate_recall@20": None,
                     "rerank_fallbacks": None})
        print(f"  {'+CRAG':<20} R@1={crag['recall@1']:.3f} R@5={crag['recall@5']:.3f} "
              f"MRR@5={crag['mrr@5']:.3f} nDCG@5={crag['ndcg@5']:.3f} Faith={crag['faithfulness']:.3f} "
              f"Cite={crag['citation_acc']:.3f} p50={_ms(crag['p50_ms'])}ms p95={_ms(crag['p95_ms'])}ms (n={crag['n']})")

        trials_dir = os.path.join("docs", "trials")
        os.makedirs(trials_dir, exist_ok=True)
        csv_path = os.path.join(trials_dir, f"{trial_id}_w2_ablation.csv")
        md_path = os.path.join(trials_dir, f"{trial_id}_w2_ablation.md")
        _write_csv(rows, csv_path)
        _write_md(rows, trial_id, md_path)
        print(f"\n✓ 已存 {csv_path} + {md_path}")
    finally:
        delete_eval_collection(writer)
        print(f"✓ 已清理本次评测集合 {writer.collection_name}")


def _write_csv(rows, path):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["config", *COLS, "n"])
        for r in rows:
            w.writerow([r["config"]] + [_f(r.get(c), "") for c in COLS] + [r.get("n", "")])


def _delta(a, b):
    """a-b, 任一为None返回None."""
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a - b
    return None


def _write_md(rows, trial_id, path):
    """Markdown报告, 结论由本次数据动态计算(不再硬编码历史数字)."""
    by = {r["config"]: r for r in rows}
    dense, hybrid = by.get("dense-only", {}), by.get("+hybrid(RRF)", {})
    f5, f20 = by.get("funnel top5->rerank5", {}), by.get("funnel top20->rerank5", {})
    crag = by.get("+CRAG", {})

    label_cn = {"candidate_recall@5": "候选R@5", "candidate_recall@20": "候选R@20", "recall@1": "Recall@1",
                "recall@5": "Recall@5", "mrr@5": "MRR@5", "ndcg@5": "nDCG@5", "faithfulness": "Faith★",
                "citation_acc": "Cite★", "p50_ms": "p50(ms)", "p95_ms": "p95(ms)", "rerank_fallbacks": "fallback"}
    lines = [
        "# DeepRAG 检索/生成 消融 + 漏斗对照",
        "",
        f"> Trial: **{trial_id}** · Golden Set: **{len(GOLDEN)}** 问 × 16 文档(8主题+8语义近似干扰项), 独占评测集合.",
        "> 漏斗口径: hybrid召回 top_k → parent去重 → cross-encoder精排 → top5.",
        "> ★ Faith/Cite 为生成器自评(verifier), 检索指标为客观计算.",
        "",
        "| Config | " + " | ".join(label_cn[c] for c in COLS) + " | n |",
        "|---|" + "---|" * (len(COLS) + 1),
    ]
    for r in rows:
        cells = []
        for c in COLS:
            v = r.get(c)
            cells.append(f"{v:.0f}" if c.endswith("_ms") and isinstance(v, (int, float)) else _f(v))
        lines.append(f"| **{r['config']}** | " + " | ".join(cells) + f" | {r.get('n', '-')} |")

    lines += ["", "## 结论要点(本次trial动态生成)", ""]
    headroom = _delta(f20.get("candidate_recall@20"), f20.get("candidate_recall@5"))
    if headroom is not None:
        lines.append(f"- 候选池 headroom: top_k=20 候选召回 R@20={_f(f20.get('candidate_recall@20'))} "
                     f"vs 同池前5 R@5={_f(f20.get('candidate_recall@5'))}(差 {_f(headroom)}), "
                     "即候选池扩大的可精排上限增益。")
    if f5 and f20:
        d_r1 = _delta(f20.get("recall@1"), f5.get("recall@1"))
        d_mrr = _delta(f20.get("mrr@5"), f5.get("mrr@5"))
        lines.append(f"- 漏斗对照 top5→rerank5 vs top20→rerank5: R@1 {_f(f5.get('recall@1'))} vs {_f(f20.get('recall@1'))}"
                     f"(Δ={_f(d_r1)}), MRR@5 {_f(f5.get('mrr@5'))} vs {_f(f20.get('mrr@5'))}(Δ={_f(d_mrr)}), "
                     f"p95 {_ms(f5.get('p95_ms'))}ms vs {_ms(f20.get('p95_ms'))}ms。")
    if hybrid and f20:
        d_recall = _delta(f20.get("recall@5"), hybrid.get("recall@5"))
        lines.append(f"- 精排增益: top20→rerank5 最终 R@5={_f(f20.get('recall@5'))} "
                     f"vs hybrid未精排 R@5={_f(hybrid.get('recall@5'))}(Δ={_f(d_recall)}); "
                     f"dense-only R@5={_f(dense.get('recall@5'))}。")
    fb = f20.get("rerank_fallbacks")
    if fb is not None:
        lines.append(f"- rerank服务稳定性: 两个漏斗配置合计 fallback {fb}/{len(GOLDEN) * 2} 次(非0需检查rerank端点)。")
    if crag:
        lines.append(f"- CRAG Agent: R@5={_f(crag.get('recall@5'))} Faith={_f(crag.get('faithfulness'))} "
                     f"Cite={_f(crag.get('citation_acc'))} p50={_ms(crag.get('p50_ms'))}ms(含LLM生成延迟)。")
    lines += [
        "",
        f"**样本量限制**: 当前 golden 仅 {len(GOLDEN)} 问(smoke baseline), 单次运行, 指标差异在噪声范围内,",
        "不作为简历/对外数字; golden v2(≥50条)定版后按同口径重跑替换本报告。",
    ]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    asyncio.run(main())

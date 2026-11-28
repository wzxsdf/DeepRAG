# W2消融矩阵: dense-only / +hybrid(RRF) / 漏斗对照(top5→rerank5 vs top20→rerank5) / +CRAG(agent)
# 每次运行创建独占 eval_<uuid> Qdrant集合, 结束后仅清理本次集合; 报告写入 docs/trials/<trial_id>_* 不覆盖历史基线
# golden v2(>=50问×48文档)定版口径; CRAG行含重试计数与p95长尾归因(逐问题latency/retries/grounded)

import asyncio
import csv
import os
import sys
from datetime import datetime

from dotenv import load_dotenv

if sys.platform == "win32":
    import io
    # line_buffering: 重定向到文件时禁用块缓冲(20261127实测: 块缓冲把挂死前的trace吞掉,
    # 误判"日志冻结无输出"; 行缓冲保证每条trace即时落盘, 卡死时能看到卡在哪个节点)
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)

load_dotenv()

from src.evaluation.ablation import (create_eval_writer, delete_eval_collection, eval_crag,
                                     eval_crag_unanswerable, eval_retrieval, ingest_eval_set)
from src.evaluation.golden import DISTRACTOR_IDS, DOCUMENTS, GOLDEN, GOLDEN_VERSION, UNANSWERABLE

CONFIGS = [
    ("dense", "dense-only"),
    ("hybrid", "+hybrid(RRF)"),
    ("funnel_top5_rerank5", "funnel top5->rerank5"),
    ("funnel_top20_rerank5", "funnel top20->rerank5"),
]
COLS = ["candidate_recall@5", "candidate_recall@20", "recall@1", "recall@5", "mrr@5", "ndcg@5",
        "faithfulness", "citation_acc", "judge_answer", "judge_grounded", "judge_citation",
        "p50_ms", "p95_ms", "rerank_fallbacks", "retries_total", "retried_n", "judge_n"]
N_TOPICS = len(DOCUMENTS) - len(DISTRACTOR_IDS)


def _f(v, default="-"):
    """数值格式化, None→'-'; 整数型指标(fallback/n)不带小数."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return default
    return str(int(v)) if isinstance(v, int) else f"{v:.3f}"


def _ms(v):
    return f"{v:.0f}" if isinstance(v, (int, float)) else "-"


def _keep_system_awake(enable: bool):
    """评测期间阻止Windows进入待机(20261126实测: 机器待机会静默杀死20min+长跑,
    日志冻结在半途且无Traceback). ES_CONTINUOUS|ES_SYSTEM_REQUIRED, 挂在主asyncio线程;
    非Windows平台no-op."""
    if sys.platform != "win32":
        return
    import ctypes
    ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
    ctypes.windll.kernel32.SetThreadExecutionState(
        ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if enable else 0))


async def main(only: str = "all", resumable: bool = False, max_q: str | None = None,
               merge_from: str | None = None, idx_from: str | None = None,
               idx_to: str | None = None):
    """only: all(默认) / retrieval(仅漏斗) / crag(仅CRAG+不可答).
    resumable: CRAG逐问checkpoint到临时文件, 配合--max-q分块续跑(后台长跑会被~20min静默杀死);
    merge_from: 预载此前retrieval-only trial的CSV行(漏斗数字确定可复用, 免重跑5分钟)."""
    import tempfile
    _keep_system_awake(True)
    trial_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    print("=" * 104)
    print(f"DeepRAG 消融+漏斗对照 trial={trial_id} (golden {GOLDEN_VERSION}: {len(GOLDEN)}问 × {len(DOCUMENTS)}文档"
          f"{' · 仅CRAG' if only == 'crag' else ''}{(' · 续跑ckpt') if resumable else ''})")
    print("=" * 104)

    # --ckpt-name: 断点文件命名空间(不同代码版本的验证跑用不同名字, 避免混入旧结果)
    ckpt = None
    if resumable:
        name = os.getenv("EVAL_CKPT_NAME", "")
        suffix = f"_{name}" if name else ""
        ckpt = os.path.join(tempfile.gettempdir(), f"deeprag_eval_crag{suffix}.jsonl")
    rows: list[dict] = []
    crag, una = {}, None  # ponytail: retrieval-only模式也要定义(修复UnboundLocalError@_write_md)
    if merge_from:  # 预载漏斗行(检索确定性, 数字与重跑逐位一致)
        import csv as _csv
        with open(merge_from, encoding="utf-8") as f:
            for r in _csv.DictReader(f):
                row: dict = {"config": r["config"]}
                for c in COLS:
                    v = (r.get(c) or "").strip()
                    if v and v != "-":
                        try:
                            row[c] = float(v)
                        except ValueError:
                            pass
                if r.get("n"):
                    row["n"] = r["n"]
                rows.append(row)
        print(f"已从 {merge_from} 预载 {len(rows)} 行漏斗结果")

    writer = create_eval_writer()
    try:
        n_docs = await ingest_eval_set(writer)
        print(f"评测集合 {writer.collection_name}: 入库 {n_docs} chunks\n")

        # 窗口指纹(20261127): SiliconFlow rerank分数跨时间窗系统性漂移(同对20:23窗口0.9+/凌晨窗口0.1x),
        # 固定参考对打分入档, 使跨窗口trial可解释/可比
        from src.retrieval.embedder import embed_query as _eq
        from src.retrieval.reranker import rerank as _rerank
        from src.storage.protocol import RetrievedDocument as _RD
        _ref = _RD(chunk_id="ref", document_id="ref_doc", version_id="v", parent_id=None,
                   content="jieba是Python中文分词库,基于前缀词典与动态规划实现精确模式切分,支持自定义词典。",
                   locator={}, score=0.0, retrieval_source="dense")
        _rr = await _rerank("中文分词在项目里用的什么库?", [_ref], top_n=1)
        _fp = _rr[0].score
        print(f"窗口指纹: 参考对rerank分数={_fp:.3f} (<0.5提示当前窗口rerank行为异常, 跨窗trial需标注)\n")

        if only in ("all", "retrieval"):
            for cfg, label in CONFIGS:
                m = await eval_retrieval(cfg, writer)
                rows.append({"config": label, **m, "faithfulness": None, "citation_acc": None})
                print(f"  {label:<20} candR@5={_f(m.get('candidate_recall@5'))} candR@20={_f(m.get('candidate_recall@20'))} "
                      f"R@1={m['recall@1']:.3f} R@5={m['recall@5']:.3f} MRR@5={m['mrr@5']:.3f} nDCG@5={m['ndcg@5']:.3f} "
                      f"p50={_ms(m['p50_ms'])}ms p95={_ms(m['p95_ms'])}ms fallback={m.get('rerank_fallbacks', '-')}")

        if only in ("all", "crag"):
            crag, una = {}, None
            print("\n  +CRAG(agent工具检索, 含生成指标, 调LLM较慢)...")
            crag = await eval_crag(writer, ckpt_path=ckpt,
                                   max_questions=int(max_q) if max_q else None,
                                   idx_from=int(idx_from) if idx_from else None,
                                   idx_to=int(idx_to) if idx_to else None)
            if crag.get("partial"):
                print(f"[partial] CRAG断点进度 {crag['done_n']}/{crag['n']} "
                      f"— 再次运行同一命令续跑(本块截止, 未产出报告)")
                return
            rows.append({"config": "+CRAG", **crag, "candidate_recall@5": None, "candidate_recall@20": None,
                         "rerank_fallbacks": None})
            print(f"  {'+CRAG':<20} R@1={crag['recall@1']:.3f} R@5={crag['recall@5']:.3f} "
                  f"MRR@5={crag['mrr@5']:.3f} nDCG@5={crag['ndcg@5']:.3f} Faith={crag['faithfulness']:.3f} "
                  f"Cite={crag['citation_acc']:.3f} p50={_ms(crag['p50_ms'])}ms p95={_ms(crag['p95_ms'])}ms "
                  f"retried={crag['retried_n']}/{crag['n']}(共{crag['retries_total']}次) (n={crag['n']})")
            if crag.get("judge_n"):
                print(f"    judge(独立异模型): answer_correct={crag['judge_answer']:.3f} "
                      f"grounded={crag['judge_grounded']:.3f} citation={crag['judge_citation']:.3f} "
                      f"(n={crag['judge_n']})")
            else:
                print("    judge: 未产出(CHAT_JUDGE_MODEL未配置或调用全败, Faith/Cite为verifier自评口径)")

            print(f"\n  不可答负样本({len(UNANSWERABLE)}问, 诚实降级率)...")
            una = await eval_crag_unanswerable(writer)
            print(f"    诚实降级率={una['honest_rate']:.3f}({una['honest_n']}/{una['n']}) "
                  f"sources={una['sources']} p50={_ms(una['p50_ms'])}ms p95={_ms(una['p95_ms'])}ms")

        trials_dir = os.path.join("docs", "trials")
        os.makedirs(trials_dir, exist_ok=True)
        csv_path = os.path.join(trials_dir, f"{trial_id}_w2_ablation.csv")
        md_path = os.path.join(trials_dir, f"{trial_id}_w2_ablation.md")
        _write_csv(rows, csv_path)
        _write_md(rows, trial_id, md_path, una=una)
        print(f"\n✓ 已存 {csv_path} + {md_path}")
    finally:
        delete_eval_collection(writer)
        _keep_system_awake(False)
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


def _write_md(rows, trial_id, path, una=None):
    """Markdown报告, 结论由本次数据动态计算(不再硬编码历史数字)."""
    by = {r["config"]: r for r in rows}
    dense, hybrid = by.get("dense-only", {}), by.get("+hybrid(RRF)", {})
    f5, f20 = by.get("funnel top5->rerank5", {}), by.get("funnel top20->rerank5", {})
    crag = by.get("+CRAG", {})

    label_cn = {"candidate_recall@5": "候选R@5", "candidate_recall@20": "候选R@20", "recall@1": "Recall@1",
                "recall@5": "Recall@5", "mrr@5": "MRR@5", "ndcg@5": "nDCG@5", "faithfulness": "Faith★",
                "citation_acc": "Cite★", "judge_answer": "Judge答准★", "judge_grounded": "Judge支撑★",
                "judge_citation": "Judge引用★", "judge_n": "judge n",
                "p50_ms": "p50(ms)", "p95_ms": "p95(ms)", "rerank_fallbacks": "fallback",
                "retries_total": "重试次", "retried_n": "重试问数"}
    lines = [
        "# DeepRAG 检索/生成 消融 + 漏斗对照",
        "",
        f"> Trial: **{trial_id}** · Golden {GOLDEN_VERSION}: **{len(GOLDEN)}** 问 × {len(DOCUMENTS)} 文档"
        f"({N_TOPICS}主题+{len(DISTRACTOR_IDS)}语义近似干扰项), 独占评测集合.",
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
                     f"Cite={_f(crag.get('citation_acc'))} p50={_ms(crag.get('p50_ms'))}ms(含LLM生成延迟); "
                     f"重试 {crag.get('retries_total', 0)} 次 / {crag.get('retried_n', 0)} 问触发重试。")
        if crag.get("judge_n"):
            lines.append(f"- **独立judge(P1②, 异模型对照参考答案)**: answer_correct={_f(crag.get('judge_answer'))} "
                         f"grounded={_f(crag.get('judge_grounded'))} citation_correct={_f(crag.get('judge_citation'))} "
                         f"(n={crag.get('judge_n')}); 此口径替代verifier自评(Faith★/Cite★自评1.000无区分度)。")
        else:
            lines.append("- 独立judge未产出(CHAT_JUDGE_MODEL未配置或调用全败): Faith★/Cite★为verifier自评口径, "
                         "1.000无区分度, 不作为对外质量数字。")
        tail = crag.get("tail") or []
        if tail:
            lines += ["", "### p95长尾归因(lat≥p95的问题, 按延迟降序)", "",
                      "| 问题 | 延迟(ms) | 重试次数 | R@5 | grounded |",
                      "|---|---|---|---|---|"]
            for t in tail[:5]:
                q = t["q"] if len(t["q"]) <= 24 else t["q"][:24] + "…"
                lines.append(f"| {q} | {t['lat_ms']:.0f} | {t['retries']} | {_f(t.get('recall@5'))} "
                             f"| {'✓' if t.get('grounded') else '✗'} |")
            retried_share = (sum(1 for t in tail if t["retries"] > 0) / len(tail)) if tail else 0
            lines += ["", f"长尾{len(tail)}问中 {retried_share:.0%} 触发agent重试; "
                          f"重试占比高则长尾为重试共性问题(考虑收紧verifier阈值/检索置信度短路), "
                          f"低则为个例波动。"]
    if una:
        honest_rate = una.get("honest_rate")
        lines += ["", "### 不可答负样本(诚实降级, P1②)", "",
                  f"诚实降级率 **{_f(honest_rate)}**({una.get('honest_n')}/{una.get('n')}) — "
                  "期望: grade判全部无关→降级tool_agent(citations为空且source≠kb_grounded); "
                  "编造kb_grounded引用即计不诚实。", "",
                  "| 问题 | 延迟(ms) | source | citations | 诚实 |",
                  "|---|---|---|---|---|"]
        for r in una.get("per_question", []):
            q = r["q"] if len(r["q"]) <= 24 else r["q"][:24] + "…"
            lines.append(f"| {q} | {r['lat_ms']:.0f} | {r['source']} | {r['citations']} "
                         f"| {'✓' if r['honest'] else '✗'} |")
        src_hist = una.get("sources") or {}
        lines += ["", f"source分布: {src_hist}"]
    v2_note = (f"golden {GOLDEN_VERSION} 定版口径({len(GOLDEN)}问×{len(DOCUMENTS)}文档+{len(UNANSWERABLE)}不可答); "
               "单次运行仍受随机噪声影响, "
               "对外引用前建议多轮运行取均值, 并按同口径保留trial记录以追踪基线漂移。") if GOLDEN_VERSION >= "v2" else \
        (f"当前 golden 仅 {len(GOLDEN)} 问(smoke baseline), 单次运行, 指标差异在噪声范围内, "
         "不作为简历/对外数字; golden v2(≥50条)定版后按同口径重跑替换本报告。")
    lines += ["", f"**样本量口径**: {v2_note}"]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    def _arg(flag, default=None):
        return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default
    asyncio.run(main(_arg("--only", "all"),
                     resumable="--resumable" in sys.argv,
                     max_q=_arg("--max-q"),
                     merge_from=_arg("--merge-from"),
                     idx_from=_arg("--idx-from"),
                     idx_to=_arg("--idx-to")))

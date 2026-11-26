# 基线聚合: 扫描 docs/trials/*_w2_ablation.csv, 输出各配置指标 mean±std 到 docs/trials/baseline.md
# 用法: python scripts/aggregate_baseline.py [trial_id ... | --v2]
#       缺省=全部golden v3口径trial; --v2=聚合v2历史口径(不同golden版本数字不可平均)
# 口径: 只聚合 md 头部带对应 "Golden vN" 标记的trial(smoke等历史口径排除)
# 用途: 改动前后各跑N轮同口径评测, 对比本表判断回退/增益(单轮差异在噪声内, 以±std为准)
import csv
import statistics
import sys
import io
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

TRIALS_DIR = Path("docs/trials")
# 聚合指标(列名→显示名); CRAG行额外聚合的指标单独处理(P1②含独立judge口径)
RETRIEVAL_METRICS = ["recall@1", "recall@5", "mrr@5", "ndcg@5", "p50_ms", "p95_ms"]
CRAG_METRICS = ["faithfulness", "citation_acc", "judge_answer", "judge_grounded", "judge_citation",
                "retries_total"]
FORMAT_INT = {"retries_total", "judge_n"}


def load_trials(trial_ids=None, marker: str = "Golden v3"):
    """返回 [(trial_id, {config: {metric: float}})]; 仅指定口径(显式指定trial_id时不过滤口径)."""
    trials = []
    for path in sorted(TRIALS_DIR.glob("*_w2_ablation.csv")):
        tid = path.name.split("_w2_ablation")[0]
        if trial_ids and tid not in trial_ids:
            continue
        md = TRIALS_DIR / path.name.replace(".csv", ".md")
        # 不同golden口径的数字不可平均: 缺省只聚合 md 头部带对应 "Golden vN" 标记的trial
        if not trial_ids and not (md.exists() and marker in md.read_text(encoding="utf-8")[:400]):
            print(f"- 跳过非{marker}口径trial: {tid}")
            continue
        rows = {}
        with open(path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                cfg = row["config"]
                rows[cfg] = {}
                for key, val in row.items():
                    if key in ("config", "n") or not val:
                        continue
                    rows[cfg][key] = float(val)
        trials.append((tid, rows))
    return trials


def _fmt(values, key):
    """mean±std(样本std, n>=2); 单点只显示值."""
    if not values:
        return "-"
    mean = statistics.mean(values)
    if len(values) < 2:
        return f"{mean:.0f}" if key in FORMAT_INT else f"{mean:.3f}"
    std = statistics.stdev(values)
    return (f"{mean:.0f}±{std:.0f}" if key in FORMAT_INT else f"{mean:.3f}±{std:.3f}")


def main():
    args = sys.argv[1:]
    marker = "Golden v2" if "--v2" in args else "Golden v3"
    trial_ids = [a for a in args if not a.startswith("--")] or None
    trials = load_trials(trial_ids, marker=marker)
    assert trials, "docs/trials 下无可聚合的 trial CSV"
    configs = []
    for _, rows in trials:
        for cfg in rows:
            if cfg not in configs:
                configs.append(cfg)

    cols = RETRIEVAL_METRICS + CRAG_METRICS
    lines = [
        f"# DeepRAG golden({marker.split()[-1]}) 基线(trial间 mean±std)",
        "",
        f"> 由 `scripts/aggregate_baseline.py` 聚合生成({marker}口径); 参与trial({len(trials)}): "
        + ", ".join(f"`{tid}`" for tid, _ in trials),
        "> 改动前后各跑N轮同口径评测, 对比本表判断回退/增益; 单轮差异在噪声内, 以±std为准.",
        "> judge_*为独立异模型口径(CHAT_JUDGE_MODEL), 历史trial未配置judge时显示'-'.",
        "",
        "| Config | " + " | ".join(cols) + " | 轮数 |",
        "|---|" + "---|" * (len(cols) + 1),
    ]
    for cfg in configs:
        cells = []
        for key in cols:
            vals = [rows[cfg][key] for _, rows in trials if cfg in rows and key in rows[cfg]]
            cells.append(_fmt(vals, key))
        n_rounds = sum(1 for _, rows in trials if cfg in rows)
        lines.append(f"| **{cfg}** | " + " | ".join(cells) + f" | {n_rounds} |")

    out = TRIALS_DIR / "baseline.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"✓ 已写入 {out} (trials={len(trials)}, configs={len(configs)})")


if __name__ == "__main__":
    main()

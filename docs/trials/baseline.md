# DeepRAG golden(v3) 基线(trial间 mean±std)

> 由 `scripts/aggregate_baseline.py` 聚合生成(Golden v3口径); 参与trial(1): `20261126_202308`
> 改动前后各跑N轮同口径评测, 对比本表判断回退/增益; 单轮差异在噪声内, 以±std为准.
> judge_*为独立异模型口径(CHAT_JUDGE_MODEL), 历史trial未配置judge时显示'-'.

| Config | recall@1 | recall@5 | mrr@5 | ndcg@5 | p50_ms | p95_ms | faithfulness | citation_acc | judge_answer | judge_grounded | judge_citation | retries_total | 轮数 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **dense-only** | 0.839 | 0.938 | 0.887 | 0.895 | 1041.135 | 1219.382 | - | - | - | - | - | - | 1 |
| **+hybrid(RRF)** | 0.823 | 0.938 | 0.879 | 0.888 | 1083.215 | 1266.060 | - | - | - | - | - | - | 1 |
| **funnel top5->rerank5** | 0.903 | 0.938 | 0.927 | 0.923 | 1960.372 | 2210.706 | - | - | - | - | - | - | 1 |
| **funnel top20->rerank5** | 0.903 | 0.944 | 0.937 | 0.926 | 2221.522 | 2432.269 | - | - | - | - | - | - | 1 |
| **+CRAG** | 0.952 | 0.927 | 0.960 | 0.933 | 6704.418 | 16234.822 | 0.984 | 0.984 | 0.839 | 0.984 | 0.984 | 1 | 1 |

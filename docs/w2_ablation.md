# DeepRAG 检索/生成 消融评估

> Golden Set: **10** 个问题 × 16 文档(8主题 + 8语义近似干扰项). 
> 从单路 dense 逐步叠加 hybrid RRF → cross-encoder rerank → CRAG Agent, 验证各模块增益.
> ★ Faith/Cite 为生成器自评(verifier), 检索指标为客观计算.

| Config | Recall@1 | Recall@5 | MRR | nDCG@5 | Faith★ | Cite★ | p50(ms) | p95(ms) | n |
|---|---|---|---|---|---|---|---|---|---|
| **dense-only** | 0.800 | 0.900 | 0.850 | 0.861 | - | - | 88 | 124 | 10 |
| **+hybrid(RRF)** | 0.800 | 0.900 | 0.850 | 0.861 | - | - | 61 | 97 | 10 |
| **+rerank** | 0.900 | 0.900 | 0.900 | 0.869 | - | - | 453 | 636 | 10 |
| **+CRAG** | 0.800 | 0.850 | 0.850 | 0.818 | 1.000 | 1.000 | 26877 | 37785 | 10 |

## 结论要点

- 本组数据中 hybrid(RRF) 与 dense-only 持平(BGE-M3 已良好区分语义干扰项 + dense 0.65 主导权重);
  hybrid 的价值在服务端融合、抗共有词噪声的鲁棒性, 以及更低的 p50 延迟。
- rerank(cross-encoder) 将 Recall@1 / MRR 从 0.80 提升至 0.90, 验证精排对 top-1 精度的增益。
- CRAG Agent 检索质量与 +rerank 相当(略低: agentic 自主查询+评分过滤会丢弃部分golden文档),
  换来 grounded 生成与自纠循环, 端到端 p50≈27s(多轮工具检索+评分+校验的延迟代价)。

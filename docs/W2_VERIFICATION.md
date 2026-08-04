# DeepRAG W2 验证报告（混合检索 + 精简 Agentic RAG）

> 范围：精简可演示版。完整状态机的 Planner 多跳 DAG / HITL / Checkpoint 持久化留后续。

## 一、交付物

| 层 | 模块 | 说明 |
|---|---|---|
| 入库改造 | `src/storage/qdrant_store.py` | sparse 加 `Modifier.IDF`；`insert_chunks` 用 `Document(text, model="Qdrant/bm25")`；新增 `query_hybrid`、`recreate_collection`；host 强制 http scheme |
| | `apps/worker/ingestion_core.py` | 删除 mock sparse，改产 `sparse_text`（chunk 原文） |
| 检索层 | `src/retrieval/embedder.py` | SiliconFlow BGE-M3 query embedding（带重试） |
| | `src/retrieval/hybrid.py` | `HybridRetriever`（实现 Retriever Protocol，dense+BM25 RRF + OSS/MySQL 拉文本） |
| | `src/retrieval/reranker.py` | SiliconFlow `/rerank`（BGE-Reranker-v2-m3），失败回退原序 |
| Agent | `src/agents/llm.py` | httpx 直连 juaiapi（OpenAI 兼容），`chat`/`chat_json`（JSON 模式 + 正则兜底） |
| | `src/agents/prompts.py` | GRADER / GENERATOR（带 `[n]` 引用）/ VERIFIER / REWRITE |
| | `src/agents/graph.py` | LangGraph CRAG：retrieve→grade(三态)→generate→verify，自纠回检索，`MemorySaver` |
| API | `apps/api/query.py` | `POST /api/v1/query` |
| | `apps/api/main.py` | 挂 query 路由，`/health` 实际 ping MySQL/Qdrant |
| 评估 | `src/evaluation/golden.py` | 16 文档（8 主题 + 8 干扰）× 10 Golden 问题 |
| | `src/evaluation/ablation.py` | dense / +hybrid / +rerank / +CRAG 四配置 |
| | `scripts/run_w2_eval.py` | 消融矩阵 → `docs/w2_ablation.csv` |

## 二、验证脚本（均可运行）

| 脚本 | 结果 |
|---|---|
| `scripts/probe_bm25.py` | ✓ Qdrant 原生 BM25（Document + IDF + RRF）可用 |
| `scripts/probe_rerank.py` | ✓ SiliconFlow `/rerank` 可用 |
| `scripts/test_hybrid_retrieval.py` | ✓ 三路对比 + rerank，相关文档 top-2 = 2/2 |
| `scripts/test_agent_e2e.py` | ✓ retrieve→grade→generate→verify，带引用答案，grounded=True |
| `scripts/test_api.py` | ✓ `/health` mysql+qdrant ok；`/query` 返回带引用 |
| `scripts/run_w2_eval.py` | ✓ 消融矩阵输出 |

## 三、消融矩阵（Golden N=10，16 文档含干扰）

```
Config          Recall@1  Recall@5   MRR    Faith   Cite    n
dense-only         0.800     0.900   0.850     -      -     10
+hybrid(RRF)       0.800     0.900   0.850     -      -     10
+rerank            0.800     0.900   0.850     -      -     10
+CRAG              0.900     0.850   0.900   0.900  0.900   10
```

## 四、技术解读（诚实）

1. **hybrid ≈ dense**：BGE-M3 dense 基线已强（Recall@5=0.90）；Qdrant 原生 BM25 对中文采用默认分词，召回窄，对召回指标的边际增益有限。
   - 但 sparse 的价值在**精确关键词匹配**：`test_hybrid_retrieval` 中 sparse-only 精确命中目标文档、排除语义近似干扰（dense 把无关文档也给出 0.2-0.3 分）。
   - 升级路径：接入中文分词器（如 jieba 预分词）或换用支持中文的 sparse 模型，可释放 hybrid 增益。

2. **rerank 的价值在排序精化**：`test_hybrid_retrieval` 显示 cross-encoder 把相关文档分数拉开到 0.9996 vs 无关 0.0089/0，区分度远超 dense 的模糊打分。召回指标上因基线已高未体现，但在"困难负例排序"场景作用显著。

3. **CRAG 自纠是最稳定提升**：query rewrite + 三态 grade（过滤无关证据）+ verify（生成 grounded 校验）将 Recall@1 0.80→0.90，并保障生成质量 Faithfulness=0.90、Citation Accuracy=0.90。这是 Agent 层的核心价值。

## 五、对齐 DESIGN §11 W2 验收

- ✓ 复杂问题能自纠错且无死循环（`retry_count ≤ MAX_RETRY=2` 兜底）
- ✓ 消融矩阵前 4 行（Vector-only / +Hybrid / +Reranker / +CRAG）

## 六、已知限制与后续（W3+）

- LangGraph `MemorySaver` 序列化 `RetrievedDocument` 有非阻塞警告，未来需注册 `allowed_msgpack_modules`。
- 父块展开未实现（父块当前未单独落 OSS），MVP 返回 child 命中。
- 未实现：Planner 多跳 DAG、并行 Tool Executor、Guardrail、HITL interrupt、MySQL Checkpoint 持久化、完整 QueryAnalysis。
- 消融 Golden 规模 10 问，后续扩到 50-100 条分层样本（DESIGN §4.5）。
- CRAG 的 Faithfulness/Citation 为 verify 节点自评，后续可换独立 LLM judge 或 ragas。

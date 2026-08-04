# DeepRAG W3 增强验证报告

> W3 在 W2（混合检索 + 精简 CRAG）基础上增强：父块展开、多轮对话、Planner 多跳、中文分词实验。

## 一、各模块验证

### W3.1 中文分词实验（负面发现，已回退）
- **假设**：jieba 预分词后 Qdrant BM25 按词匹配，释放中文 hybrid 增益。
- **实验**：入库 sparse_text 与检索 query 都走 `tokenize_zh`（jieba 分词 + 空格连接）。
- **结果**：hybrid Recall@1 反而从 0.80 降到 0.70。
- **根因**：分词引入"共有词噪声"——"法国首都"分词为"法国 首都"，"首都"与"英国首都"共有，BM25 误命中干扰项 london；而**原文（字符级）BM25 严格匹配"法国首都"字符序列，反而更精确**。
- **决策**：回退到原文 BM25（hybrid=dense=0.80，干净）。保留 `src/retrieval/tokenizer.py` 作为实验性工具（适用于术语密集/中英混排场景）。
- **价值**：负面发现也是深度——理解 BM25 共有词噪声、字符级 vs 词级分词的权衡。

### W3.2 父块展开 ✓
- 入库时父块（parent_content）按 parent_id 单独落 OSS（`parent_chunks/{pid}.txt`）。
- 检索命中 child 后拉父块，`RetrievedDocument.parent_content` 返回完整上下文。
- 验证（`scripts/test_parent_expansion.py`）：3 child 命中，均返回 60 字符父块上下文。

### W3.3 多轮对话持久化 ✓
- 应用层历史持久化（MySQL `conversation_messages` 表），`conversation_id` 跨请求复用。
- graph 的 generate 节点接收 `conversation_history`，拼入最近 3 轮供 LLM 理解指代。
- 验证（`scripts/test_multi_turn.py`）：Q1 答 BGE-M3，Q2 用代词"它"→ 正确答出"1024维"（基于 Q1 上下文）；持久化 4 条消息。

### W3.4 Planner 多跳（精简版）✓
- route 节点 LLM 判断 simple/complex；complex 则分解 2-3 个子查询。
- retrieve 节点对子查询 `asyncio.gather` 并行检索 + 融合去重（按 chunk_id 保留最高分）。
- 修了一个并行 bug：多 retrieve 共享 MySQLStore 连接池的 close 竞争 → 改为每次 retrieve 独立 MySQLStore。
- 验证（`scripts/test_multi_hop.py`）：对比问题"LangGraph vs LlamaIndex"→ route 判 complex + 分解 3 子查询 → 并行融合命中两框架文档 → 综合对比答案。

## 二、最终消融矩阵（W3 后，Golden N=10）

```
Config          Recall@1  Recall@5   MRR    Faith   Cite    n
dense-only         0.800     0.900   0.850     -      -     10
+hybrid(RRF)       0.800     0.900   0.850     -      -     10
+rerank            0.800     0.900   0.850     -      -     10
+CRAG              0.800     0.850   0.850   1.000  1.000   10
```

## 三、波动说明（诚实）

- **检索指标（R@1/R@5/MRR）**：dense/hybrid/rerank 在 BGE-M3 强 dense 基线下接近（0.80），因评估问题多 simple 且 dense 已强；hybrid（加权 RRF）持平 dense，未引入噪声（W3.1 分词实验证明原文 BM25 更优）。
- **rerank 的提升有波动**：cross-encoder 在 hybrid top1 已正确时无提升空间，仅在 top2-3 错排时修正；不同运行间 R@1 在 0.80-0.90 间波动。
- **CRAG 的稳定增量在生成质量**：Faithfulness=1.0、Citation Accuracy=1.0 稳定——grade（过滤无关证据）+ verify（grounded 校验）保障答案有据可循、引用真实。这是 CRAG 相对纯检索的真正价值。
- **CRAG R@1 波动**：route（W3.4）对 simple 评估问题判 simple 走单查，但 route/grade 的 LLM 判断偶发影响最终 documents，R@1 在 0.80-1.00 间波动。
- **改进方向**：评估多次取均值；扩大 Golden 到 50-100 条降低方差；grade/verify 改独立 LLM judge 或 ragas。

## 四、对齐 DESIGN

- ✓ 父子分块 + parent expansion（§4.2）
- ✓ 多轮对话（§4.3 conversation_id，应用层持久化，非 PostgresSaver）
- ✓ Planner 多跳（§4.3 精简版：route + 子查询并行 + 融合，非完整 DAG 框架）
- ✓ 加权 RRF 融合（dense 主导）
- 待后续：Guardrail、HITL interrupt、完整 QueryAnalysis、authority_score、PostgresSaver/MySQL checkpointer

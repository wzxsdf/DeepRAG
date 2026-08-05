# 混合检索与 CRAG 生成

> 核心问答链路：从 query 到可信答案。涉及 `src/retrieval/hybrid.py`、`reranker.py`、`src/agents/graph.py`、`prompts.py`。

## 一、整体链路

```
query
  │
  ▼
[混合检索 HybridRetriever]   dense(BGE-M3) + sparse(Qdrant BM25) → 服务端 RRF 融合
  │
  ▼
[Rerank 精排]                BGE-reranker-v2-m3 重排, 取 top_n
  │
  ▼
[grade 相关性评估]            批量 LLM 判每条证据相关性
  │  ├─ 无相关 → tool_agent 兜底(软拒)
  │  └─ 有相关 ↓
  ▼
[generate 生成]               基于证据 + [n] 引用, small-to-big(child引用+parent上下文), 流式
  │
  ▼
[verify 校验]                 grounded(无幻觉) + citations_valid(引用有效)
  │  ├─ 双通过 → END (kb_grounded)
  │  ├─ 不达标 & retry<2 → rewrite → 回 retrieve (自纠)
  │  └─ 不达标 & 达上限 → END (fallback_unverified, 诚实降级)
```

## 二、混合检索

**`HybridRetriever`** (`src/retrieval/hybrid.py:17`)，核心方法 `retrieve(query, filters, top_k)` (`hybrid.py:43-95`)。

### 2.1 dense + sparse 服务端融合

```
query ──► embed_query(BGE-M3) ──► dense_vec[1024]
query ──► query_text(原文)    ──► sparse(BM25 输入)
                 │
                 ▼
   qdrant.query_hybrid_async(dense_vec, query_text, tenant_id, kb_ids, limit)
                 │
                 ▼   (Qdrant 服务端做 BM25 + 加权 RRF 融合, dense 主导)
            融合结果 list[RetrievedDocument]
```

**关键设计**：
- **融合在服务端**（Qdrant）完成，客户端不再二次融合，零额外开销
- BM25 走 Qdrant 原生 `Document(text=..., model="Qdrant/bm25")` + IDF modifier
- 加权 **RRF（倒数排名融合）**，**dense 主导**（实测对中文 QA 更稳）
- 结果统一打标 `retrieval_source="hybrid_rrf"` (`hybrid.py:92`)

### 2.2 取文本路径（零 IO 优化）

- **content 优先取 Qdrant payload**（入库时已存）—— 零 IO 直取
- 仅老数据（payload 无 content）才回查 MySQL 再拉 OSS
- **parent 上下文**：收集 `parent_id`，`asyncio.gather` 并发拉 OSS `parent_chunks/{pid}.txt`，写入 `parent_content`

> 💡 HybridRetriever 经 lifespan **构造注入**共享 MySQLStore（阶段 5），内部 lazy connect，不再每次检索新建连接。

### 2.3 检索过滤

`RetrievalFilter` (`src/storage/protocol.py:7-13`)：
- `tenant_id`（必填，多租户隔离）
- `kb_ids`（多 KB 检索，None = 不限——前端可勾选特定知识库）

## 三、Rerank 精排

**`reranker.py`**

- **模型**：`BAAI/bge-reranker-v2-m3`（`reranker.py:14`），SiliconFlow `/rerank` 接口
- **入口**：`async def rerank(query, docs, top_n=5)` (`reranker.py:17-43`)
- **参数**：`top_n = min(top_n, len(docs))`，`return_documents=False`（只取 index+score）
- **跨语言**：reranker-v2-m3 原生多语言，中英混排都强
- **副作用**：原地改写 `doc.score` 和 `doc.retrieval_source="rerank"`
- **容错**：异常回退原序（RRF 序）裁剪 top_n，不阻塞主流程

## 四、CRAG 三态（graph.py）

整体在 `src/agents/graph.py`，`MAX_RETRY = 2`。LangGraph 1.x `Command(goto=...)` 声明式路由。

### 4.1 grade 节点（批量相关性评估）

**`_grade_node`** (`graph.py:139-158`)

- 批量 **1 次 LLM** 判全部证据：`GRADER_BATCH_PROMPT`，每条截到 400 字符
- 输出 JSON `{"grades":[{"index":1,"relevant":true/false}]}`，过滤保留 relevant 子集
- **三段兜底**：
  - 无文档 → `goto="tool_agent"`（软拒兜底）
  - grades 空 / 解析异常 → 保守视为全相关（不丢证据）
  - relevant 空 → `goto="tool_agent"`；否则 `goto="generate"`

> W3.5 改进：无证据不再硬拒，改走 tool_agent 软拒兜底（可能调工具或常识答）。

### 4.2 generate 节点（基于证据生成 + 引用）

**`_generate_node`** (`graph.py:195-207`)

**small-to-big 在生成的落点**：
- **child 作为可引用单元**，带 `[n]` 编号
- **parent_content 作为上下文**，拼成 `(上下文: {parent_content})` 附加到证据末尾
- 好处：引用最小化（指向精确片段），上下文最大化（扩展父段）

**多轮历史**：取最近 3 轮 Q/A 拼到 prompt 前置（"供理解指代"）。

**GENERATOR_PROMPT 三条硬规则**：
1. 每个事实陈述后用 `[n]` 标注引用
2. 不要编造证据外的信息
3. 若证据不足以回答，直接说明缺什么

**流式**：经 `_stream_answer()` (`graph.py:51-70`)，逐 token 经 `get_stream_writer()` 推前端打字机效果。

### 4.3 verify 节点（双校验）

**`_verify_node`** (`graph.py:210-223`)

`VERIFIER_PROMPT` 判两项：
- `grounded`：回答是否完全被证据支持（无幻觉）
- `citations_valid`：每个 `[n]` 引用是否真实对应证据编号

**三分支路由**：
- 双双通过 → `source="kb_grounded"`，`goto=END`
- 不通过 & `retry_count < MAX_RETRY(=2)` → `goto="rewrite"`
- 不通过但达上限 → `source="fallback_unverified"`，`goto=END`（诚实降级免责标注）

### 4.4 rewrite 节点（查询改写 + 自纠）

**`_rewrite_node`** (`graph.py:190-192`)

- `REWRITE_PROMPT`：补充关键词、消除代词与歧义，只输出改写后问题
- 写回 `state["query"]`（检索用 query，不改原 `question`），`retry_count += 1`
- 边 `rewrite → retrieve`，构成自纠环

```
retrieve → grade → generate → verify ─(不达标)─→ rewrite → retrieve ...
                                      │
                                      └(达标)─→ END
```

**生成质量**：`Faith / Cite` 双指标在 CRAG 路径下稳定为 1.0，得益于"generate 强制 [n] + verify 双校验 + MAX_RETRY 自纠"闭环。

## 五、kb 模式检索节点

**`_retrieve_node`** (`graph.py`)：kb 模式固定**单次检索 + rerank**（mode 路由后删除了 simple/complex 分流与 ReAct 多跳检索）：

```python
async def _retrieve(state):
    docs = await retriever.retrieve(state["query"], RetrievalFilter(tenant_id, kb_ids), top_k=5)
    return {"documents": await rerank(state["query"], docs, top_n=5)}
```

- 多跳 / 多源需求由 **agent 模式的 `plan_agent`**（规划 + 逐步执行）覆盖，不再在检索节点内做 ReAct
- 复杂多步问答用户选 agent 模式；kb 模式专注单次严谨检索 + CRAG 自纠

> 💡 旧的 `run_search_agent`（ReAct 多跳检索）已删——mode 路由后该路径零调用（plan_agent 的 KB 步骤走 `_exec_plan_step` 直接检索）。生成/校验的 LLM 调用经 ModelGateway（`chat_stream`/`chat_json`，阶段 2）。

## 六、数据结构

### `RetrievedDocument` (`protocol.py:15-26`)

```python
class RetrievedDocument:
    chunk_id: str           # child UUID
    document_id: str
    version_id: str
    parent_id: str | None   # 父块锚点(small-to-big)
    content: str            # child 文本(可引用单元)
    locator: dict           # {page, heading_path, line, table} 回链定位
    score: float            # 融合分 / rerank 分
    retrieval_source: str   # dense / sparse / hybrid_rrf / rerank
    parent_content: str     # W3.2 父块扩展(生成上下文)
```

### `Retriever` Protocol (`protocol.py:28-37`)

最小接口 `retrieve(query, filters, top_k) -> list[RetrievedDocument]`——"W1 最小接口不预埋 V2"。`HybridRetriever` 是其生产实现。

## 七、工程亮点

1. **服务端融合**（零客户端再排）：dense + BM25 + RRF 全在 Qdrant 服务端
2. **payload 零 IO**：content 存 payload，检索免回查 OSS/MySQL
3. **parent 并发拉取**：`asyncio.gather` 拉 parent_content，生成阶段零 IO
4. **rerank 失败降级**：异常回退 RRF 序，不阻塞
5. **规则快通道省 ~4s**：明显闲聊跳过 route LLM
6. **MAX_RETRY=2 防死循环**：自纠有上限
7. **Command 声明式路由**：路由逻辑内聚到节点
8. **诚实降级**：不达标标 `fallback_unverified`，不假装可信

---

上一篇：[02-文档入库流水线](02-文档入库流水线.md) ｜ 下一篇：[04-意图分层路由](04-意图分层路由.md)

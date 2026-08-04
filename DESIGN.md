# DeepRAG 设计文档

> 企业级 Agentic RAG 知识与故障诊断平台 · 聚焦版(MVP)
>
> **目标**:2-3 周交付可演示、可量化的 Agentic RAG,简历主打 AI/Agent 方向。
> **文档日期**:2026-07-31 · **版本**:v1.0

---

## 0. 文档说明

本文档是 DeepRAG 开发期间的**唯一参照设计**,定义产品边界、技术栈、架构、核心模块、数据模型、API、安全、评测、部署与落地计划。

**设计原则**:把深度堆在「检索 + Agent + 评测」三角上,其余全部最小化或砍掉。每项技术必须绑定一个 Golden Dataset 上的对照实验--没数字的不写进简历。

**参考来源**:基于《DeepRAG_Agentic_RAG系统技术方案.docx》裁剪聚焦而成。原方案是企业级全员上阵,本聚焦版面向个人简历项目,砍掉 GraphRAG/Text-to-SQL/MCP/多模态/长期记忆等 2-3 周做不出深度的模块,集中火力攻 Hybrid 检索、LangGraph CRAG Agent、评测消融三大主战场。

---

## 1. 设计总纲:裁剪逻辑与聚焦

一句话:**先做深,再做广。** 简历上"做深 3 个点 + 有数字"远胜"列了 10 个框架但没指标"。

| 模块 | 原方案 | 本版决策 | 理由 |
|---|---|---|---|
| Hybrid 检索 | Dense+BM25+RRF+Rerank | ✅ 保留做深 | RAG 基本功,可消融量化 |
| LangGraph Agent | 7 节点全状态机 | ✅ 保留做深(主战场) | Agent 方向简历核心 |
| CRAG 自纠错 | Correct/Ambiguous/Incorrect | ✅ 保留做深 | 体现 Agent 自主性 |
| 引用+Verifier+拒答 | 完整 | ✅ 保留 | 可信 RAG,面试高频 |
| Golden Dataset+消融 | 完整 | ✅ 保留做深 | 简历能写真实数字的关键 |
| GraphRAG(Neo4j) | M4 | ❌ 砍掉 | 2-3 周做不深,易成堆砌 |
| Text-to-SQL | M4 | ❌ 砍掉(V2 亮点) | 同上 |
| MCP/多模态/长期记忆/Reflection | M6 | ❌ 全砍 | 选不了,不深不如不做 |
| 多租户 RBAC | 完整 | 🔸 简化为单租户+ACL 过滤 | 保留"权限前置"思想即可 |
| OpenSearch(BM25) | 标配 | ❌ 换 BGE-M3 sparse | 省一个重组件,见 §2 亮点 |
| MySQL | 标配 | 🔸 换 PostgreSQL | JSONB 存 AgentState/Checkpoint 更自然,LangGraph PostgresSaver 原生支持 |
| 阿里云 OSS | 标配 | ✅ 保留 | STS 直传是云原生亮点 |
| OTel/Prometheus | 完整 | 🔸 MVP 用 Langfuse+structlog | V2 再加,演示够用 |
| ACK/K8s | 生产 | ❌ Docker Compose | MVP 本地一键起 |
| 前端 | - | 🔸 轻量 React 单页 | SSE+Trace 可视化 |

---

## 2. 技术栈选型

| 层级 | 选型 | 选型理由(面试可答) |
|---|---|---|
| API | FastAPI + Pydantic v2 | 异步、类型约束、SSE 友好 |
| Agent 编排 | LangGraph | 显式状态图、条件边、Checkpoint、HITL、流式事件 |
| 模型网关 | LiteLLM | 统一多模型、成本记录、降级路由 |
| 元数据/状态 | PostgreSQL 16 | JSONB 存 AgentState,LangGraph `PostgresSaver` 原生 Checkpoint |
| 对象存储 | 阿里云 OSS | 直传+STS+生命周期,云原生亮点 |
| 向量检索 | Qdrant | 一个实例同时做 dense+sparse,hybrid query 原生支持 RRF |
| 缓存/队列 | Redis | 语义缓存、分布式锁、Celery broker |
| 异步任务 | Celery | 入库流水线状态机 |
| 文档解析 | Docling + PyMuPDF | 结构感知、表格、页码、阅读顺序 |
| Embedding | BGE-M3 | ⭐ 一次推理同时输出 dense+sparse+colBERT,多语言强 |
| Reranker | BGE-Reranker-v2-m3 | Cross-Encoder 精排 |
| 评测 | RAGAS + 自建 Golden Dataset | 召回/忠实度/引用指标 |
| 可观测 | Langfuse + structlog | Trace、成本、反馈、统一 trace_id |
| 部署 | Docker Compose | 一键本地演示 |
| 前端 | Vite+React+Tailwind 单页 | SSE Trace + 引用可视化 |

### 两个关键选型亮点(面试加分项)

**① BGE-M3 一次推理双路向量**
传统 Hybrid 要 dense 模型 + BM25 引擎两套。BGE-M3 一个模型同时输出 dense(语义,1024 维)和 sparse(词项级,学习型稀疏,多语言下优于统计型 BM25)。Qdrant Query API 用 `prefetch` 同时查 dense 和 sparse,`fusion: rrf` 融合。**一个 embedding 调用 + 一个 Qdrant 实例 = 完整 Hybrid**,部署最轻。这是 2025-2026 主流轻量 Hybrid 方案。

**② LangGraph + PostgresSaver**
AgentState 和 Checkpoint 直接落 PG 的 JSONB,Agent 可中断、可恢复、可回放。这是"可观测、可恢复 Agent"的落点,而不是把 LLM 调用串起来。

---

## 3. 系统架构

三平面架构:在线查询(低延迟)+ 离线入库(异步)+ 评测可观测(统一 trace_id)。

```
                    ┌─────────────────────────────────────────────┐
   在线查询平面      │  FastAPI ─SSE─> LangGraph Agent 状态机         │
   (低延迟)          │   Router->Retriever->CRAG->Generator->Verifier │
                    └──────┬────────────────────────────┬───────────┘
                           │ Qdrant(dense+sparse)       │ PG(AgentState/会话)
                           │ OSS(原文片段)              │ Redis(语义缓存)
                    ┌──────┴────────────────────────────┴───────────┐
   离线入库平面      │  Celery Worker: 解析->切分->增强->Embedding->索引 │
   (异步)            │  PG(文档/版本/Chunk 元数据) + OSS(原始/解析产物)   │
                    └────────────────────────────────────────────────┘
                    ┌────────────────────────────────────────────────┐
   评测可观测平面    │  Golden Dataset + RAGAS + 消融实验 + Langfuse    │
   (统一 trace_id)   │  question->plan->tools->evidence->answer->cost  │
                    └────────────────────────────────────────────────┘
```

### 架构原则

- **检索与生成解耦**:Retriever/Reranker/Generator 均通过 Protocol 接口替换。
- **状态显式化**:计划、工具结果、重试次数、预算写入统一 AgentState。
- **权限前置**:ACL 必须参与召回过滤,不能依赖模型判断。
- **异步优先**:解析、OCR、Embedding、索引由 Celery 执行。
- **可测量**:任何"效果提升"必须通过固定数据集、对照实验证明。
- **渐进复杂**:简单问题走短路径,复杂问题才启动 Planner、CRAG、Verifier。

---

## 4. 核心模块设计

### 4.1 入库流水线(质量壁垒)

**状态机**:`UPLOADED -> PARSING -> CHUNKING -> ENRICHING -> EMBEDDING -> INDEXING -> READY | FAILED -> RETRY/DEAD_LETTER`

**流程**:
1. 客户端通过后端签发的 OSS STS 临时凭证或预签名 URL 直传文件,避免大文件经过 API。
2. 上传确认后创建 `document_version` 与 `indexing_job`,Worker 下载指定 version。
3. Docling 解析,中间 JSON、页面图片、表格写回 OSS。
4. 切分 + 增强 + Embedding,写入 Qdrant。
5. Qdrant 写入成功后版本状态置 READY,原子切换 `active_version_id`。

**深度点 ①:Parent-Child + 结构感知切分**
- Child(200-500 tok)用于精准召回,Parent(1000-2000 tok)用于生成补上下文。
- 按标题/代码块/表格边界动态切,绝不切断表格和代码。
- Child 存 `parent_id`;Qdrant 只索引 Child,生成时按 `parent_id` 批量取 Parent。
- Baseline:固定长度切分,用于对照实验。

**深度点 ②:BGE-M3 双路向量一次生成**
- 每个 Chunk 一次 BGE-M3 推理,同时拿 dense(1024)和 sparse 词项权重。
- 分别写入 Qdrant 的 dense 和 sparse 命名空间。
- 元数据落 PG(chunks 表),原文片段落 OSS(引用回链)。

**OSS 对象规划**:
```
oss://{bucket}/{env}/
  raw/{doc_id}/{ver_id}/source.pdf
  parsed/{doc_id}/{ver_id}/document.json     # Docling 结构化产物
  pages/{doc_id}/{ver_id}/page-0001.png
  tables/{doc_id}/{ver_id}/table-0001.parquet
  chunks/{doc_id}/{ver_id}/chunk-{id}.txt    # 原文片段(引用回链)
  exports/{conversation_id}/answer.md
  evaluations/{dataset_version}/golden.jsonl
```

**OSS 安全**:STS 最小权限(限定 key 前缀、大小、类型、有效期)、VPC Endpoint、SSE-KMS、内容类型校验、病毒扫描、访问日志关联 trace_id。

### 4.2 Hybrid 检索链路(深度主战场之一)

```
Query Rewrite / Multi-Query / HyDE
        │
   ┌────┴───── BGE-M3 一次编码 ─────┐
   ↓ dense                          ↓ sparse
Qdrant dense search            Qdrant sparse search
   │                                 │
   └──────── RRF 融合 ───────────────┘
                 │
        BGE-Reranker-v2-m3 (Cross-Encoder 精排)
                 │
        MMR 去重 + 上下文压缩
                 │
           Top-K (5-10) 证据包
```

**查询理解(QueryAnalysis)**:识别意图、实体、时间/版本过滤、复杂度(simple/medium/complex)、是否需拆子问题。对话代词先独立化改写。

```python
class QueryAnalysis(BaseModel):
    intent: str
    rewritten_query: str
    keywords: list[str]
    entities: list[str]
    filters: dict[str, str]            # 时间范围、产品版本、ACL
    complexity: Literal["simple", "medium", "complex"]
    routes: list[Literal["dense", "sparse", "tool"]]
    need_decomposition: bool
```

**自适应 Top-K**:简单问题 top_k=5,复杂多跳 top_k=10 + 多轮检索。

**检索路由**:
| 问题类型 | 执行路径 |
|---|---|
| 闲聊/无需知识 | 直接回答,不检索 |
| 单事实 | Hybrid -> Rerank -> Answer |
| 跨文档复杂 | Planner -> Multi-hop -> Evidence Fusion |
| 检索不足 | Rewrite / HyDE / 明确拒答 |

### 4.3 LangGraph Agent 状态机(深度主战场之二--简历核心)

```
START -> Guardrail -> Router ──simple──-> Retriever ──┐
                     │                              │
                  complex                           │
                     ↓                              │
                 Planner(生成步骤DAG)                │
                     │                              │
              Tool Executor(并行)                   │
              ├ dense retrieval                     │
              ├ sparse retrieval                    │
              └ multi-hop 子查询                    │
                     │                              │
                     └──────────-> Evidence Fusion ←──┘
                                       │
                                 CRAG Grader ◄────┐
                                  ┌────┼────┐      │
                              correct  ambiguous incorrect
                                │      │       │   │
                                │   Rewrite    换源/  │
                                │   +重检索    拒答   │
                                │      └──────────────┘
                                ↓      (最多 N 轮,预算兜底)
                           Generator(带引用)
                                │
                           Verifier ◄──────┐
                           ├ 通过 -> END     │
                           └ 证据不足 ──────┘ (回补检索)

        高风险动作 -> interrupt_before -> Human-in-the-loop
```

**AgentState(落 PG JSONB)**:
```python
class AgentState(TypedDict):
    tenant_id: str
    conversation_id: str
    original_query: str
    query_analysis: dict
    plan: list[PlanStep]          # 带依赖的步骤 DAG
    evidence: list[Evidence]
    tool_results: list[ToolResult]
    draft_answer: str | None
    verification: dict | None     # 忠实度、引用正确性、下一动作
    retry_count: int              # CRAG 重检索次数
    token_budget: int             # 硬预算
    deadline_ms: int              # 延迟硬预算
    trace_id: str
```

**四个体现深度的设计点(面试重点)**:

**① CRAG 三分支评估器**--判断召回质量:
- `correct`:证据相关且充分 -> 直接生成
- `ambiguous`:证据部分相关 -> 改写 query / HyDE 重检索(循环)
- `incorrect`:证据无关 -> 换数据源或明确拒答

Grader = 规则(rerank 分数阈值 + 命中数)+ 轻量模型判断,避免每次都用大模型拖延迟。

**② 预算与循环控制**--Agent 稳定的命门:
- 最大迭代 N=3,超过后基于现有证据回答或拒答
- token / 工具调用次数 / 总延迟 三重硬预算
- 连续两轮证据集合无新增 -> 提前终止(避免空转)
- 确定性错误(如工具 404)直接降级,不盲目重试;超时才指数退避

**③ 工具并行**--Planner 生成带依赖的 DAG,无依赖步骤并行执行(dense/sparse/子查询同时跑),降低 P95。

**④ Checkpoint + HITL**--每节点前后写 PostgresSaver,Agent 可中断恢复;高风险动作 `interrupt_before` 进人工确认。

### 4.4 引用与可信

**Evidence 结构**(每条证据带溯源):
```python
class Evidence(BaseModel):
    evidence_id: str
    document_id: str
    version_id: str
    chunk_id: str
    locator: dict          # {page, heading_path, line, table}
    quote: str             # 原文片段
    retrieval_source: str  # dense / sparse
    rerank_score: float
    authority_score: float
    acl_snapshot: str
```

**Generator 输出 claim->citation 映射**,Verifier 逐条校验引用是否真实支持结论。

**拒答触发条件**:
- 无有效证据或低于最低相关度阈值
- 关键证据相互冲突且无法定权威版本
- 问题超出知识库范围且用户未授权外部搜索
- 用户无权限访问相关文档
- Verifier 发现关键结论无引用支持
- 工具连续失败或任务超出成本/延迟预算

**权限前置**:ACL 在 Qdrant filter 阶段完成,不依赖模型判断。缓存键必须含 ACL hash + 知识库版本。

### 4.5 评测体系(简历能写数字的关键--深度主战场之三)

**Golden Dataset**:50-100 条分层样本(量不在多在精):
- 单事实 / 多跳 / 版本比较 / 表格 / 无答案 / 冲突文档 / 越权 / 提示注入
- 每条含:期望答案、相关文档 ID、期望引用、问题类型、难度、ACL 上下文

```json
{
  "question": "支付服务出现 ConnectionPoolTimeout 的可能原因?",
  "expected_answer": "...",
  "relevant_document_ids": ["d_001", "d_042"],
  "expected_citations": ["c_001#page3"],
  "question_type": "multi_hop",
  "difficulty": "hard",
  "acl_context": ["engineering"]
}
```

**⭐ 消融实验矩阵(简历数字来源)**:

| 方案 | Recall@10 | MRR | Faithfulness | Citation Acc. | P95 |
|---|---|---|---|---|---|
| Vector-only (dense baseline) | _实测_ | _实测_ | _实测_ | _实测_ | _实测_ |
| + Hybrid (加 sparse) | _实测_ | _实测_ | _实测_ | _实测_ | _实测_ |
| + Reranker | _实测_ | _实测_ | _实测_ | _实测_ | _实测_ |
| + Query Rewrite | _实测_ | _实测_ | _实测_ | _实测_ | _实测_ |
| + CRAG 自纠错 | _实测_ | _实测_ | _实测_ | _实测_ | _实测_ |

每行只在上一行基础上加一项,用同一数据集、同一 `experiment_config` 跑。这张表跑出来,简历就能写真实百分比,且仓库有可复现配置佐证。**这是区分"堆框架"和"真懂 RAG"的分水岭。**

**指标体系**:
| 层级 | 核心指标 |
|---|---|
| 检索 | Recall@K、Precision@K、MRR、NDCG、Hit Rate、Rerank uplift |
| 生成 | Faithfulness、Answer Relevance、Context Precision/Recall |
| 引用 | Citation Correctness、Citation Completeness、引用覆盖率 |
| 拒答 | 无答案识别率、误拒答率、越权拦截率 |
| Agent | 任务成功率、路由准确率、工具成功率、无效调用率、循环次数 |
| 工程 | P50/P95 延迟、吞吐、错误率、每请求 Token 与费用 |

**指标纪律**:不预先编造提升百分比。README、简历、演示中的所有数字必须来自固定版本数据集和可复现实验配置。

---

## 5. 数据模型与存储设计

### 5.1 PostgreSQL 职责

PG 保存知识库配置、文档元数据、版本、Chunk 元数据、索引任务、ACL、会话、消息、Agent Checkpoint、评测实验、反馈。大文本正文与二进制不直接写 PG,只存 OSS key、hash、大小、版本、状态。

| 表 | 关键字段 | 说明 |
|---|---|---|
| tenants / users | tenant_id, user_id, status | 租户与用户主体 |
| knowledge_bases | id, tenant_id, embedding_model, config_json | 知识库及检索配置 |
| documents | id, kb_id, title, active_version_id, acl_policy | 逻辑文档与当前版本 |
| document_versions | id, document_id, oss_key, sha256, status | 不可变版本与 OSS 对象 |
| chunks | id, version_id, parent_id, page, heading_path, content_hash | Chunk 元数据与索引映射 |
| indexing_jobs | id, version_id, stage, status, retry_count | 入库任务状态机 |
| conversations / messages | id, user_id, role, content, citations | 会话与引用结果 |
| agent_checkpoints | thread_id, checkpoint_id, state_json | LangGraph 恢复点(PostgresSaver) |
| eval_runs / eval_cases | run_id, dataset_version, config, metrics | 评测实验与结果 |
| feedback | message_id, rating, reason, correction | 在线反馈与回归样本 |

### 5.2 数据一致性

- PG 是文档状态和索引可见性的事实源;Qdrant 只保存可重建索引。
- 创建版本先写 PG PENDING,再写 OSS;上传确认后进入解析状态。
- 索引用 `version_id` 作隔离键;所有检索默认过滤 `active_version_id`。
- 删除文档:软删除 + outbox event,后台清理 Qdrant/OSS。
- 通过 outbox/inbox、幂等键、补偿任务处理跨存储最终一致性。

### 5.3 Qdrant 集合设计

- 单集合,Point 用 `chunk_id`。
- dense 向量(1024)+ sparse 向量(BGE-M3 词项)。
- payload:`tenant_id`、`kb_id`、`document_id`、`version_id`、`parent_id`、`page`、`heading_path`、`acl_tags`、`active_version_id`。
- 检索 filter:`active_version_id == X AND acl_tags ⊇ user_acl`。

---

## 6. API 设计

### 6.1 核心 API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | /api/v1/chat/stream | SSE 流式执行 Agent |
| POST | /api/v1/uploads/presign | 获取 OSS 临时上传信息(STS/预签名) |
| POST | /api/v1/documents/confirm | 确认上传并创建文档版本 |
| GET | /api/v1/documents/{id} | 查询文档、版本和索引状态 |
| DELETE | /api/v1/documents/{id} | 软删除并触发索引清理 |
| POST | /api/v1/reindex | 按版本或知识库重建索引 |
| GET | /api/v1/jobs/{id} | 查询异步任务状态 |
| POST | /api/v1/evaluations | 启动离线评测 |
| GET | /api/v1/traces/{trace_id} | 查看 Agent 完整链路 |

### 6.2 SSE 事件流

```
planning -> retrieving -> tool_call -> tool_result
-> generating -> verifying -> citation -> completed
                              \-> error / waiting_for_approval
```

前端实时画 Trace:Router 决策、Planner 步骤、工具调用、CRAG 纠错、引用、置信度。

---

## 7. 工程结构

```
deeprag/
├── apps/api/              # FastAPI + SSE
├── apps/worker/           # Celery 入库
├── apps/web/              # Vite+React 单页(Trace 可视化)
├── src/agents/            # LangGraph nodes/state/policies ← 核心
├── src/ingestion/         # Docling/parsers/chunkers
├── src/retrieval/         # dense/sparse/rerank/fusion ← 核心
├── src/generation/        # prompts/citations/verifier
├── src/storage/           # pg/oss/qdrant/redis
├── src/evaluation/        # golden dataset/ragas/ablation ← 核心
├── src/observability/     # langfuse/structlog
├── tests/{unit,integration,evaluation,security}/
├── deploy/compose/        # docker-compose.yml
└── pyproject.toml
```

**核心接口契约**(业务层只依赖 Protocol,SDK 封装在 infra 层):
```python
class Retriever(Protocol):
    async def retrieve(
        self, query: str, filters: RetrievalFilter, top_k: int
    ) -> list[Evidence]: ...

class ObjectStore(Protocol):
    async def presign_upload(self, key: str, expires: int) -> PresignedUpload: ...
    async def put(self, key: str, data: bytes) -> ObjectMeta: ...
    async def get(self, key: str) -> bytes: ...
```

OSS SDK、Qdrant SDK 都封装在 infrastructure 层,便于测试替身、云环境迁移、单组件替换。

---

## 8. 安全与权限

| 风险 | 控制措施 |
|---|---|
| 文档恶意指令 | 检索内容视为不可信数据;系统指令与证据分区;检测越权语句 |
| 系统提示泄露 | 拒绝输出系统配置/密钥/工具 Schema;日志脱敏 |
| 工具参数注入 | Pydantic Schema 校验、枚举约束、路径/域名白名单 |
| OSS 越权 | STS 最小权限、固定 key 前缀、短时凭证、服务端二次校验 |
| 缓存越权 | 缓存键含 tenant、ACL hash、index version;敏感响应不共享 |
| 权限泄露 | ACL 召回前过滤,对抗测试 |

---

## 9. 可观测性

- **Langfuse**:Prompt、模型、输入输出、工具调用、成本、人工反馈。
- **structlog**:统一 `trace_id`、`tenant_id`、`conversation_id`、`tool_call_id`;敏感字段脱敏。
- **告警(MVP 轻量)**:P95 超标、拒答率异常、索引 backlog、评测回归。
- V2 再加 OpenTelemetry 跨服务 Trace + Prometheus/Grafana。

---

## 10. 部署

### 10.1 本地开发

Docker Compose 启动:FastAPI、Worker、PostgreSQL、Redis、Qdrant、Langfuse。OSS 用测试 Bucket;所有外部依赖通过环境变量和 Secret 文件注入,仓库只提交 `.env.example`。

### 10.2 性能策略

- 多路检索和互不依赖的工具并行执行。
- Embedding 批处理;查询和文档 Embedding 分别缓存。
- 按复杂度动态选择小模型/大模型、是否启动 Verifier。
- Rerank 前先去重与粗过滤,减少 Cross-Encoder 开销。
- 语义缓存必须含 tenant、ACL hash、知识库版本、模型版本。
- SSE 先返回计划和检索进度,降低用户感知等待。

---

## 11. 落地计划(2-3 周)

| 周 | 交付 | 验收 |
|---|---|---|
| **W1 基线闭环** | FastAPI+PG+Qdrant+OSS 直传;Docling 解析+Parent-Child;BGE-M3 双路索引;dense 检索+引用;SSE;Docker Compose | 端到端能问答,带页码引用;Golden Dataset 初版(30 条) |
| **W2 检索+Agent 深度** | Hybrid(dense+sparse+RRF)+Reranker;Query Rewrite;LangGraph 状态机;Router+CRAG 三分支;Verifier+拒答;预算/Checkpoint | 复杂问题能自纠错且无死循环;消融表跑出前 4 行 |
| **W3 评测+生产化** | 消融表完整 5 行;Langfuse trace;语义缓存;ACL 过滤;安全测试;React Trace 页;演示脚本+README | 3-5 分钟演示完整链路;简历数字全部有实验佐证 |

**每周完成定义**:类型检查+单元测试通过;迁移脚本+测试数据齐全;固定数据集上生成可复现实验报告;trace_id 可定位耗时与证据;README/架构图/API 同步更新。

---

## 12. 简历表达模板(数字用实测替换)

> **DeepRAG - 企业级 Agentic RAG 知识与故障诊断平台** | Python · LangGraph · FastAPI · PostgreSQL · 阿里云 OSS · Qdrant
>
> - 设计并实现基于 **LangGraph 显式状态机**的 Agentic RAG,含 Router/Planner/Retriever/CRAG/Generator/Verifier 节点;引入 **Corrective RAG 三分支自纠错**、工具并行、token/迭代/延迟三重预算控制与 PostgresSaver Checkpoint,复杂问题可自纠错且无死循环。
> - 构建基于 **BGE-M3 单模型双路向量**(dense+sparse)的 Hybrid 检索,经 RRF 融合 + BGE-Reranker 精排;通过 Parent-Child 切分与 Query Rewrite,在自建 Golden Dataset 上使 **Recall@10 提升 X%**、**Faithfulness 提升 Y%**(附可复现消融实验)。
> - 实现 **逐结论引用溯源 + Verifier 校验 + 拒答策略**,ACL 权限前置过滤;设计 PG+OSS+Qdrant 一致性方案,支持版本管理、增量索引与删除传播。
> - 建立 **RAGAS + Langfuse** 评测观测体系,通过 5 组消融实验量化每项技术增益,监控召回、引用正确率、工具成功率、Token 成本与 P95 延迟。

**纪律**:简历中的提升百分比、延迟、成本必须替换为真实实验结果,并在仓库中保留可复现评测配置。

---

## 13. 面试高频问题预判

| 主题 | 必答要点 |
|---|---|
| 为什么 Hybrid 优于纯向量? | dense 抓语义、sparse 抓精确词项/专有名词,互补;RRF 解决分数尺度不一致 |
| 为什么 BGE-M3 替代 BM25? | 一次推理出 dense+sparse,省一个引擎;sparse 是学习型稀疏,多语言下优于统计型 BM25 |
| Parent-Child 为什么? | Child 精准召回,Parent 补上下文,避免长上下文稀释检索精度 |
| CRAG 三分支怎么判? | rerank 分数阈值+命中数(规则)+轻量模型 grading;ambiguous 触发改写重检索 |
| Agent 怎么避免死循环? | 最大迭代 N、三重硬预算、连续两轮无新增证据提前终止、确定性错误直接降级 |
| 引用怎么证明支持结论? | claim->citation 映射,Verifier 逐条校验引用是否真实支持,不支持则补检索或拒答 |
| PG/OSS/Qdrant 删除不一致? | PG 是事实源,软删除+outbox 事件+补偿扫描,索引可由 PG+OSS 重建 |
| 为什么砍掉 GraphRAG? | 2-3 周做不出可量化深度,先做深核心检索+Agent,GraphRAG 列为 V2 |

---

## 14. 风险与应对

| 风险 | 表现 | 缓解 |
|---|---|---|
| 概念堆砌 | 框架多但没指标 | 每项能力绑定问题、对照、收益 |
| 入库质量差 | 召回缺页码或表格破碎 | 结构解析、抽样 QA、解析置信度 |
| Agent 不稳定 | 循环、重复调用、成本失控 | 显式状态、预算、停止条件、Checkpoint |
| 多存储不一致 | 删除后仍可召回旧内容 | 版本过滤、Outbox、补偿扫描、重建 |
| 权限泄露 | 缓存或索引返回越权文档 | 召回前 ACL、缓存隔离、对抗测试 |
| 演示依赖外网 | 模型或 OSS 不稳定 | 准备本地小模型/固定结果降级与演示数据 |

---

## 15. V2 演进(时间富余时按优先级)

1. **GraphRAG**:实体关系抽取 + Neo4j + Local Search,解决实体关系推理问题。
2. **Text-to-SQL**:只读账号 + SQL AST 白名单,解决统计明细问题。
3. **MCP 工具**:把 GitHub/工单/内部 API 封装为可发现工具。
4. **多模态**:OCR + 页面图像 + 图表联合检索。
5. **长期记忆**:Working/Episodic/Semantic Memory,经重要性/隐私/过期策略写入。
6. **OTel + Prometheus**:跨服务分布式 Trace + 指标。

**最终建议**:先交付可测量的 Hybrid RAG,再加入 LangGraph CRAG 闭环;核心检索评测稳定后,从 V2 中选最能支持业务问题的 2-3 项做深。DeepRAG 的技术深度来自可解释的检索设计、可控的 Agent 状态机、跨存储一致性、可信引用和真实评测,而不是框架数量。

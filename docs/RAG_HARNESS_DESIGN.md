# RAG Harness 架构设计

> 目标:一套**完整、可复用、可量化**的 RAG Harness 架构 —— 不只是"跑通一次的 Pipeline",而是让任何组件可替换、任何实验可复现、任何回归可拦截的骨架。
> 基于 Haystack 2.x / LlamaIndex / FlashRAG / RAGFlow / Ragas / RAGChecker / AutoRAG / GraphRAG / Mem0/Zep 的实现层调研(见 §8 对照表),全部结论有源码或论文依据。
> 文档日期: 2026-11-24 · 版本 v1.0

---

## 0. 定位与设计哲学

**Harness ≠ Pipeline**。Pipeline 是"跑一次的流程";Harness 是包在 Pipeline 外面的实验系统:

```
Harness = 数据契约 + 可插拔组件 + 编排 DSL + 实验/评测引擎 + 回归门禁 + 观测
```

三条第一性原则(每条都有反例教训):

1. **一切皆组件,一切皆配置** —— 换一个 reranker 应该只改一行 YAML,不是改三处 Python。判据来自 Haystack(组件即 YAML 节点)与 AutoRAG(config 是唯一事实源)。
2. **中间产物即数据** —— 每次运行的 query/改写/检索结果/评分/证据全部挂在样本上落盘,可断点续跑、可逐 case 归因。来自 FlashRAG 的 `dataset.update_output("retrieval_result", ...)` 黑板模式。
3. **不可测量的组件不存在** —— 任何组件接入 Harness 必须同时给出它的评测指标;评测工具本身也要被评测(RAGChecker 自带 meta-evaluation,280 对人类偏好验证指标可信度)。

**能力自检**(不满足就不是 Harness,只是 Pipeline):

| 问题 | 合格答案 |
|---|---|
| 换一个 embedding/reranker/分块策略要改几处? | 1 行 YAML |
| 跑一次消融实验要几个命令? | 1 个命令 + 1 个 scenario 文件 |
| 接入一个新指标要动几个文件? | 1 个新类(自动注册) |
| 文档更新后旧答案会过期吗? | 不会(缓存带版本失效) |
| 任何结论能复现吗? | 能,trial 目录存 config+逐 case 结果 |

---

## 1. 总体架构:五层 + 两个横切

```
┌──────────────────────────────────────────────────────────┐
│ L5 实验层   Scenario 矩阵 / 消融 / 回归门禁 / 报告产物       │
├──────────────────────────────────────────────────────────┤
│ L4 编排层   Pipeline DSL(DAG) · Agent 图(状态机) · 自适应路由 │
├──────────────────────────────────────────────────────────┤
│ L3 组件层   Parser/Chunker/Retriever/Reranker/... Protocol │
├──────────────────────────────────────────────────────────┤
│ L2 数据层   Chunk · QueryBundle · Evidence · CaseTrace      │
├──────────────────────────────────────────────────────────┤
│ L1 基础设施  ModelGateway · 向量库 · 缓存 · 存储 · 索引生命周期 │
└──────────────────────────────────────────────────────────┘
   横切①: Registry(组件/指标/场景注册表 + YAML 实例化)
   横切②: Observability(trace/span/事件流/成本, OTel 兼容)
```

分层原则:**每层只依赖下一层的契约,不依赖实现**。L4 的 Agent 图和 DAG Pipeline 共享 L3 组件 —— 这样消融实验可以同时作用于确定性 pipeline 和 agent 内部步骤(这是多数项目做不到的)。

---

## 2. L2 数据契约(核心中的核心)

所有层通过这五个数据结构通信。**先定契约,再写组件** —— 契约错了,后面全部返工。

```python
# 2.1 Chunk —— 索引与检索的原子单元
@dataclass
class Chunk:
    id: str
    content: str                    # 生成用文本
    embedding_text: str | None      # 检索用文本(可与 content 不同, RAGFlow QA 模板: 只用问题做匹配)
    parent_id: str | None           # small-to-big: 检索 child, 生成用 parent
    heading_path: list[str]         # 结构感知: ["第3章", "3.2 数据模型", "3.2.1"]
    page: int
    doc_type: str                   # chunk template 路由键: paper/laws/qa/table/...
    table_html: str | None          # 表格转 HTML 单独成块(RAGFlow), 绝不切断
    keywords: list[str]             # auto-keyword 增强层(0-30个, RAGFlow)
    questions: list[str]            # auto-question 增强(0-10个)
    acl_tags: list[str]             # 权限必须参与召回过滤
    version_id: str                 # 索引版本(检索必须过滤 active version)
    score: float = 0.0

# 2.2 QueryBundle —— LlamaIndex 关键设计: 改写只影响检索, 不污染生成
@dataclass
class QueryBundle:
    original: str                   # 用户原问题(生成/展示用)
    embedding_strs: list[str]       # 参与向量化的文本(HyDE 假想文档/multi-query 变体放这里)
    rewritten: list[str]            # 参与检索的改写(不影响生成 prompt)
    intent: str                     # chitchat/qa/multi_hop/agentic
    complexity: str                 # simple/medium/complex  → Adaptive-RAG 三级路由
    filters: RetrievalFilter        # tenant/kb_ids/acl_tags/active_version
    budget: QueryBudget             # max_llm_calls, max_latency_ms, max_cost

# 2.3 Evidence —— 证据必须携带全链路得分(归因用)
@dataclass
class Evidence:
    chunk: Chunk
    dense_score: float | None       # 各阶段得分分离, 才能归因"是召回没召回, 还是排序排错了"
    sparse_score: float | None
    fused_rank: int | None
    rerank_score: float | None

# 2.4 RetrievalResult / Generation —— 组件输出统一封包
@dataclass
class RetrievalResult:
    evidences: list[Evidence]
    strategy: str                   # dense/hybrid_rrf/rerank/hyde/...
    latency_ms: float
    token_cost: int

# 2.5 CaseTrace —— 一次评测样本的全量黑板(FlashRAG 模式)
@dataclass
class CaseTrace:
    case_id: str
    config_fingerprint: str         # 本次 run 的 config hash(复现锚点)
    query: QueryBundle
    steps: list[StepRecord]         # 每步: 组件名/输入摘要/输出摘要/延迟/token
    retrieval: RetrievalResult | None
    generation: str
    metrics: dict[str, float]       # 本 case 的所有指标(逐 case, 不是只存均值!)
```

设计要点:
- **`embedding_strs` 与 `original` 分离**(LlamaIndex `QueryBundle.custom_embedding_strs`):HyDE 生成的假想文档只参与向量检索,绝不出现在生成 prompt 里 —— 这是"改写不影响生成"的关键解耦。
- **Evidence 得分逐阶段保留**:没有分阶段得分,消融就只能看端到端数字,无法归因到"召回不足"还是"排序失误"。
- **metrics 逐 case 存**(`intermediate_data.json` 模式):均值丢失分布信息,回归分析需要逐 case diff。

---

## 3. L3 组件层:Protocol + Registry

### 3.1 组件契约(Haystack 式零侵入)

```python
class Retriever(Protocol):
    name: ClassVar[str]
    async def retrieve(self, query: QueryBundle, top_k: int) -> RetrievalResult: ...
```

- **Protocol 不继承基类**:`@register("retriever")` 装饰器只做两件事 —— 校验方法签名、注册到 `REGISTRY[slot][name]`。用户的类不必继承任何东西(Haystack 的 Component 是 runtime_checkable Protocol + 元类只做 socket 推断)。
- **组件不得原地修改输入**(Haystack 官方约束):入参 QueryBundle 视为只读,输出新对象。这是并行执行和缓存的前提。
- **每个 slot 同时给出 sync/async** 或统一 async(Haystack 要求 run/run_async 逐参数镜像,防实现漂移)。

### 3.2 组件 slot 目录与候选实现

| Slot | 职责 | 候选实现(按复杂度) |
|---|---|---|
| `parser` | 文档→结构化中间产物 | PyMuPDF blocks / DeepDoc(OCR+layout+TSR) / Docling / MinerU |
| `chunker` | 结构→Chunk 树 | fixed / sentence-window / hierarchical(2048-512-128) / semantic(断点百分位) / **doc-type template 路由**(RAGFlow) |
| `enricher` | Chunk 增强 | auto-keyword / auto-question / 封闭标签集打标(向量相似度, 零 LLM) |
| `embedder` | 文本→向量 | BGE-M3(dense+sparse 一次) / API 模型 / 本地模型 |
| `query_transformer` | QueryBundle→QueryBundle | rewrite / HyDE / sub-question(并行子查询) / step-back / decompose |
| `retriever` | QueryBundle→RetrievalResult | dense / sparse-BM25 / hybrid+加权RRF / graph dual-level / web |
| `reranker` | 重排 | cross-encoder / colbert / LLM listwise / MMR 多样性 |
| `compressor` | 证据压缩 | extractive / LLMLingua / parent-expansion / decompose-recompose(CRAG) |
| `judger` | 检索质量判路 | LLM batch grader / 轻量 NLI(0.77B 够, CRAG) / 双阈值三分类 |
| `router` | 复杂度路由 | 规则 / LLM 分类 / T5 分类器(silver-label 自监督, Adaptive-RAG) |
| `synthesizer` | 证据→答案 | compact / refine / tree-summarize / map-reduce(全局性问题) |
| `verifier` | 生成校验 | claims entailment / citation check / NLI |
| `memory` | 记忆读写 | 短期窗口+摘要 / 长期语义+时间衰减 / 实体画像 |
| `tool` | 外部动作 | kb_search / web / calc / weather / MCP |

### 3.3 YAML 实例化(Haystack 安全三件套)

```yaml
# pipeline.yaml —— config 是唯一事实源
components:
  retriever:
    type: src.retrieval.hybrid.HybridRetriever   # import 路径必须在白名单内
    init_parameters:
      weights: {dense: 0.65, sparse: 0.35}
```

反序列化必须做三件事(Haystack 的 `DeserializationError` 范式):
1. **import 路径白名单**(只允许 `src.*` 与标准库类型),屏蔽 `eval/exec/__import__/open`
2. **init_parameters 键与 `__init__` 签名逐一校验**(防 YAML 注入未知参数)
3. 失败统一抛 `DeserializationError`,绝不静默降级

---

## 4. L4 编排层:DAG 与 Agent 图双范式

### 4.1 两种执行范式,一套组件

| 范式 | 适用 | 评测友好度 |
|---|---|---|
| **DAG Pipeline**(声明式) | 确定性流程:检索→重排→生成。消融/回归的主力 | ⭐⭐⭐ 每 node 可单测、可缓存 |
| **Agent 图**(LangGraph 状态机) | 不确定流程:CRAG 自纠、多跳、工具调用 | ⭐⭐ 通过把内部步骤指向 L3 组件获得可消融性 |

关键设计:**Agent 图内部的 retrieve/grade/generate 步骤必须调用 L3 组件**,不允许 agent 代码里内联检索逻辑。否则消融实验覆盖不到 agent 路径。

### 4.2 Pipeline DSL

```yaml
# 声明式 pipeline: node_lines 分线, 每线内 nodes 串联
node_lines:
  - line_name: pre_retrieval
    nodes:
      - node_type: query_transformer
        modules: [rewrite, hyde]          # scenario 可在此矩阵展开
        strategy: {metric: retrieval_recall, k: 5}
  - line_name: retrieval
    nodes:
      - node_type: retriever
        modules: [hybrid_rrf]
      - node_type: reranker
        modules: [cross_encoder]
        strategy: {recall_top_k: 20, output_top_n: 5}
  - line_name: generation
    nodes:
      - node_type: synthesizer
        modules: [compact]
      - node_type: verifier
        modules: [claims_entailment]
```

两条来自 AutoRAG 的关键机制:
- **节点局部指标分层搜索**:检索节点用 retrieval_f1/ndcg 选型,生成节点用 faithfulness 选型 —— 上游先定,下游只在最优上游上展开,避免全组合跑全链路(组合爆炸)。
- **检索 ground truth 重映射**:换 chunk 策略后,gold_doc_ids 对应的 chunk 集合变了,评测前按 chunk→doc 归属自动重映射,保证不同分块策略可比。

### 4.3 路由与自纠(研究范式的 Harness 化)

- **三级自适应路由**(Adaptive-RAG):complexity 分类器(可先用 silver-label 自监督训练:三策略全跑,答对即标签,平手偏向简单策略)→ simple 直答 / medium 单跳 / complex 多跳迭代。实测效率收益:耗时 1.46s vs 全多跳 3.33s,F1 不降。
- **CRAG 三分类自纠**:judger 用轻量 NLI(T5-large 0.77B 即够,判断准确率 84.3% 超 ChatGPT 变体)双阈值分 Correct/Incorrect/Ambiguous → Correct 走 decompose-recompose / Incorrect 丢检索转 web search / Ambiguous 内外合并。
- **循环护栏**:所有带环的图必须有 `max_runs_per_component` 计数器(Haystack 默认 100)+ 预算型 `QueryBudget`,双保险防死循环烧钱。

### 4.4 统一事件流协议

Agent/pipeline 对外只暴露一种事件流(tagged events),前端/评测/观测共用:

```
{tag: token|tool_call|plan|plan_exec|node_span|metric, ...}
```

组件内部不直接 push 事件,由编排层把组件调用包装成 `node_span`(开始/结束/耗时/token)—— 观测与业务解耦。

---

## 5. L5 实验/评测 Harness(最能体现能力的一层)

### 5.1 Golden Dataset 管理

```yaml
# golden/v2.yaml —— 版本化, 不再是 python 列表
version: v2
cases:
  - id: gh-001
    question: "DeepRAG 的检索层用了什么技术?"
    gold_answers: ["BGE-M3 dense + Qdrant BM25 sparse + 加权 RRF + Cross-Encoder 重排"]
    gold_doc_ids: ["design_doc"]
    gold_claims:                    # claim 级标注, claim recall/faithfulness 用
      - "检索层使用 BGE-M3 生成 dense 向量"
      - "RRF 融合权重 dense 为 0.65"
    tags: {type: factual, difficulty: easy, source: manual}
    in_scope: true                  # false = 拒答测试样本
```

- **分布要求**(Ragas testset 范式):factual / multi-hop / conditional / **out-of-scope(拒答)** 四类按比例,规模 ≥50(带 bootstrap CI 的最低线),分 difficulty 三档。
- **自动扩充**:KG 式 testset 生成(文档→切分→实体抽取→关系连边→遍历图组合出题,分布 dict 控制 simple/multi_context/reasoning 比例),产物进**人工审核队列**再入 golden —— 自动生成不直接当金标准。
- **线上反哺**:生产失败 trace(低评分/用户负反馈)自动转 golden 候选(DeepEval 模式),golden 集是活的。

### 5.2 指标注册表(FlashRAG 自注册模式)

```python
class BaseMetric:
    metric_name: ClassVar[str]
    stage: ClassVar[str]            # retrieval / generation / agent / cost
    async def score(self, case: CaseTrace, golden: GoldenCase) -> float: ...

# Evaluator 用 __subclasses__() 递归自动注册 —— 新指标 = 1 个类, 零接入成本
```

| 阶段 | 指标 | 实现机制 |
|---|---|---|
| retrieval | recall@k / MRR / nDCG@k | 经典, gold_doc_ids 重映射后计算 |
| retrieval | **claim recall**(RAGChecker) | gold_claims 逐条被检索块蕴含的比例 —— 比 doc 级 recall 粒度细一个量级 |
| retrieval | context precision | AP = Σ(P@k × v_k)/|相关数|, 位置敏感, 无关块排第 1 会拉到 ~0.5 |
| generation | **faithfulness**(Ragas) | 答案分解原子 claims → 逐条 LLM entailment → 支持数/总数;可换 HHEM-2.1 开源分类器免费批量跑 |
| generation | answer relevancy | 答案反向生成 N 个问题 → 与原问题嵌入余弦均值(答得准则问题可重建) |
| generation | noise sensitivity I/II、self-knowledge(RAGChecker) | 错误 claim 归因到"相关块噪声/无关块噪声/纯幻觉";self-knowledge = 上下文没有但答对的 claim |
| generation | citation accuracy | 引用编号→chunk 实际支持该句的比例 |
| agent | 步数 / 工具准确率 / token 成本 / p50/p95 延迟 | 从 CaseTrace.steps 直接算 |
| 看板 | TRACe 四元组 | Relevance(检索准不准) / Utilization(用了没) / Adherence(忠实没) / Completeness(全不全) + 行动规则(低 R 低 U = 检索器问题; 仅低 U = 生成器问题) |

**指标按组件归因**(RAGChecker 核心思想):每个指标必须映射到一个修复动作 —— claim recall 低→改召回;NS(I) 高→检索降噪;self-knowledge 高→prompt 收紧。评测不是打分,是定位。

### 5.3 Judge 管理(LLM-as-judge 的工程纪律)

调研确认的三个偏差与对策(MT-Bench 系统性结论):

1. **自评偏差(self-enhancement)**:GPT-4 判 GPT-4 类输出偏差最大 → **judge 模型与被测生成模型必须不同家族**,judge 配置独立于业务配置。
2. **位置偏差**: pairwise 比较必须 swap 位置跑两次取均值(Balanced Position Calibration)。
3. **verbosity 偏差**: judge prompt 显式声明"长度不是质量",或按答案长度分桶看分数分布。

统计纪律:
- **样本量**:指标报 bootstrap 95% CI,不报裸均值;N<30 的"提升"一律不写结论。
- **joint checking**(RAGChecker):一次 claim 抽取,同时对 gold answer 判对错、对 chunks 判忠实性,省一半以上 judge 调用。
- judge 失败/解析失败进 `invalid_result`,计入报表而不是静默丢弃(LlamaIndex `EvaluationResult.invalid_reason` 模式)。

### 5.4 Scenario Runner(消融引擎)

```
scenarios/
  rerank_funnel.yaml          # 一个 scenario = 一组节点覆盖矩阵
```

```yaml
name: rerank_funnel
base_config: config/prod.yaml
matrix:
  retriever.top_k: [5, 10, 20, 30]        # 4 值
  reranker.enabled: [false, true]          # 2 值
metrics: [recall@5, mrr, ndcg@5, claim_recall, faithfulness, p95_ms, token_cost]
judge: {model: <与生成不同家族>, temperature: 0}
output: trials/rerank_funnel-20261124/    # trial 目录
```

Runner 行为:
1. 按 matrix 笛卡尔积生成 config 变体(节点级局部指标先筛,再全链路 —— AutoRAG 分层搜索)
2. 每个 variant 写 trial 目录:`config.yaml` + `cases.jsonl`(逐 case metrics) + `summary.csv` + 自动生成 markdown 报告(含 CI)
3. **config fingerprint** 写进每个 CaseTrace —— 报告里的任何数字都能反查到确切配置
4. 检索类 scenario 复用检索缓存(FlashRAG `retriever._save_cache()`),同 query 不同下游不重复检索

### 5.5 回归门禁(CI 集成)

```yaml
# eval_gate.yaml
gate:
  recall@5: {min: 0.85, max_regression: 0.02}      # 绝对下限 + 相对回退限
  faithfulness: {min: 0.90}
  p95_ms: {max: 8000}
  refusal_rate_out_of_scope: {min: 0.9}             # 拒答能力也是门禁项
strategy: bootstrap_ci_overlap                      # 与基线 CI 不重叠才算"提升"
```

- golden 集 ≥50 时进 CI(每 PR 跑检索指标,全量生成指标 nightly);<50 时只本地跑。
- **线上失败 trace → golden 通道**闭环,门禁越用越准。

---

## 6. L1 基础设施与横切关注点

| 关注点 | 设计 | 来源/教训 |
|---|---|---|
| ModelGateway | LLM/embedding/rerank 统一出口;**judge endpoint 与业务 endpoint 分离**;记 token 成本与延迟;失败降级策略显式声明 | Ragas 依赖注入 + MT-Bench judge 隔离 |
| 缓存 | 精确 + 语义(阈值 0.85)两级;**key 携带 kb 版本号,文档入库/删除/版本切换即失效**;缓存命中也写 trace | DeepRAG P0 隐患修正 |
| 索引生命周期 | version + 原子切换 active_version_id;**检索过滤必须带 active_version + acl_tags**;重入库幂等(content_hash 去重跳过) | DeepRAG P0 隐患修正 |
| Observability | 统一 trace/span 模型,语义对齐 OpenInference(检索 span 带 query/documents/retrieval metrics);可导出 OTel→Phoenix / Langfuse | Phoenix: trace 上叠 eval,debug 与评测共用一份数据 |
| 记忆 | 写侧:Mem0 两阶段(extraction + LLM tool-call 四操作 ADD/UPDATE/DELETE/NOOP);失效:Zep bi-temporal(**旧事实置 invalid 不物理删**,支持时间推理);长会话:Letta 分层(pin 的核心块 + 逐出递归摘要 + 可检索归档) + token 水位 70% 预警 | 三家各取一招 |
| 安全 | ACL 前置于召回过滤(不是后过滤);safety 规则层独立于业务路由 | 权限前置原则 |

---

## 7. 深度能力路线(选装,按消融数据决定去留)

按投入产出排序,每项接入后必须跑 scenario 出数字:

1. **重排漏斗修复**(recall 20-30 → rerank → top5):零新技术,白捡精度,先做。
2. **Query 变换家族**:统一 `QueryTransformer` 协议下接 rewrite / HyDE(只改 embedding_strs) / sub-question(并行子查询融合)。消融矩阵:`{none, rewrite, hyde, subq} × {top_k 5, 20}`。
3. **doc-type 模板分块**(RAGFlow):paper/laws/qa/table 各一套切分逻辑;QA 模板只用问题做匹配、答案只做生成;表格转 HTML 单块。对比 fixed 切分跑全指标。
4. **CRAG 轻量评估器**:本地 0.77B NLI 替换 LLM grader(延迟从秒级降到百毫秒级,准确率反而更高),补齐 web search 兜底分支。
5. **LightRAG 式图检索(低成本版)**:dual-level 关键词(实体词/主题词分开匹配),增量索引集合合并不重建;比 GraphRAG 社区摘要便宜两个数量级(检索 <100 tokens vs 610k)。
6. **记忆个性化 rerank**:user facts 作为 rerank 弱信号 + Mem0 式写闭环,评测走 LongMemEval 式多轮数据。

---

## 8. 设计来源对照表(每个决策有出处)

| Harness 设计 | 来源 | 具体机制 |
|---|---|---|
| Protocol 零侵入 + YAML 实例化 + 反序列化安全三件套 | Haystack 2.x | runtime_checkable Protocol、import 白名单、init 签名校验、危险内建屏蔽 |
| `embedding_strs` 与原问题分离 | LlamaIndex | `QueryBundle.custom_embedding_strs`(HyDE 只影响检索) |
| 中间产物黑板 / 逐 case 落盘 / 检索缓存 | FlashRAG | `dataset.update_output()`、`intermediate_data.json`、`_save_cache()` |
| 指标类自注册 / 统一实验入口 | FlashRAG | `BaseMetric.__subclasses__()`、`run_exp.py` 单入口 |
| 节点局部指标分层搜索 / 检索 GT 重映射 | AutoRAG | 检索节点用检索指标选型、chunk 策略变化时 GT 自动重映射 |
| claim 级指标 / 组件归因 / joint checking | RAGChecker | claim recall、噪声敏感度 I/II、self-knowledge、一次抽取多参照判定 |
| faithfulness/relevancy 计算范式 / judge 依赖注入 | Ragas | claims→entailment 比值、反向问题余弦、构造器注入 judge |
| 指标可信度自证 | RAGChecker meta-eval | 280 对人类偏好验证指标与人类判断相关系数 |
| 三级自适应路由 + silver-label 训练 | Adaptive-RAG | 复杂度分类器自监督、平手偏向简单策略 |
| CRAG 三分类 + 轻量评估器 + web 兜底 | CRAG | T5-large 0.77B、双阈值、decompose-recompose |
| 循环护栏 | Haystack | `max_runs_per_component` 计数器 |
| 分块模板 / 检索文本与生成文本分离 / 关键词增强 | RAGFlow | chunk template、QA 模板问题匹配、auto-keyword、封闭标签集 |
| 缓存失效 / 索引版本 | — | 生产事故反推(DeepRAG P0) |
| 记忆四操作维护 / bi-temporal 失效 / 分层+水位 | Mem0 / Zep / Letta | ADD/UPDATE/DELETE/NOOP、t_valid/t_invalid 不删除、token 70% 预警 |
| 回归门禁 pytest 化 | DeepEval | `assert_test` + 阈值断言 + CI CLI |
| 看板指标口径 | RAGBench TRACe | Relevance/Utilization/Adherence/Completeness + 行动规则 |

---

## 9. 落地路线图(映射到 DeepRAG 现状)

| 阶段 | 内容 | 交付判据 |
|---|---|---|
| **P0 正确性** | 缓存版本失效、RetrievalFilter 补 acl_tags+active_version、重排漏斗 20→5 | 三个 bug 修复 + 各一个回归测试 |
| **P1 数据契约** | Chunk/QueryBundle/Evidence/CaseTrace 五契约落地;现有 src 改造对齐契约 | 组件间零 dict 传参 |
| **P2 Eval Harness** | golden v2(≥50 条四类分布)、指标注册表、judge 分离 + position calibration、bootstrap CI | `python -m harness.run --scenario base` 出完整报告 |
| **P3 消融+门禁** | scenario runner、trial 目录规范、CI eval gate(golden≥50 后启用) | 恶意改坏 rerank 能被 CI 拦截 |
| **P4 深度能力** | query 变换家族、doc-type 模板分块、CRAG NLI 评估器 | 每项有消融数字,不涨不留 |
| **P5 记忆升级** | Mem0 式写管道、bi-temporal 失效、个性化 rerank | 多轮评测集数字 |

---

## 10. 收尾判据

一个 RAG 系统是否配得上"Harness"之名,过这五关:

1. 换任何组件 = 改 1 行 YAML,且 CI 自动验证不退化
2. 跑任何消融 = 1 个命令,产物可复现(config fingerprint)
3. 新指标 = 1 个类,自动进报表
4. 任何指标下降都能**归因到组件**(分阶段得分 + claim 级标注)
5. golden 集是活的(线上失败反哺),评测工具本身被 meta-eval 验证过

# DeepRAG Agent 记忆层设计方案

> 版本: v1.0 ｜ 日期: 2026-08-08
> 理论依据: 记忆四层模型 + 三工程问题 + 四种压缩方法（参考小林 Agent 记忆系列）
> 设计原则: 复用现有存储栈，不引入新依赖

---

## 一、设计目标与原则

### 1.1 目标

让 DeepRAG 从「单次问答工具」升级为「能跨会话积累、能保持多轮连贯、不会因对话变长而失忆」的真正助手。

### 1.2 核心原则

| 原则 | 说明 |
|------|------|
| **复用优先** | 长期记忆复用 Qdrant + BGE-M3，不引 Mem0/Letta/Chroma |
| **混合存储** | 语义内容进向量库，结构化偏好进关系库，热数据进 Redis |
| **闭环驱动** | 读-用-写三阶段闭环，接入现有 graph.py 状态机 |
| **渐进落地** | P0 先治最痛的「对话变长就失忆」，P3 才做程序记忆 |
| **诚实标注** | source 字段区分「有记忆支撑」vs「无记忆」，不伪装 |

---

## 二、现状诊断（设计的起点）

### 2.1 现有能力

| 能力 | 实现 | 评价 |
|------|------|------|
| 对话持久化 | MySQL `conversation_messages` 表 | ✅ 有，会话维度全量存储 |
| 多轮上下文 | graph.py `conversation_history` 拼进 prompt | ⚠️ 有，但全量拼、无窗口管理 |
| ReAct 摘要 | `SummarizationMiddleware`（仅 agent 模式） | ⚠️ CRAG 链路没有 |
| checkpoint | `MemorySaver` + 每请求新 uuid | ❌ 名存实亡，状态从不复用 |
| 独立记忆模块 | `src/memory/`（Redis+SentenceBERT） | ❌ 零接入，且引了第二个嵌入模型 |

### 2.2 三个真实痛点

1. **CRAG 链路无压缩** → 多轮对话一长就爆 token
2. **无跨会话记忆** → 换 conversation_id 一切重来，用户偏好不沉淀
3. **历史按时间全量取** → 不是按相关性取，长会话信噪比差

---

## 三、记忆分层架构

按四层模型映射到 DeepRAG 落地实现：

```
┌─────────────────────────────────────────────────────────┐
│  感知记忆  │ 用户当次输入的原始 question            │ 即时  │
├─────────────────────────────────────────────────────────┤
│  短期记忆  │ graph.py AgentState.conversation_history│ 任务内│
│           │ (messages 工作台) + 窗口管理 + 摘要      │      │
├─────────────────────────────────────────────────────────┤
│  长期记忆  │ Qdrant agent_memory collection (语义检索)│ 持久  │
│           │ ├─ 情节记忆: 具体问答经历                │      │
│           │ ├─ 语义记忆: 抽象提炼的规律              │      │
│           │ └─ 程序记忆: 可复用流程(YAGNI,P3再做)    │      │
├─────────────────────────────────────────────────────────┤
│  实体记忆  │ MySQL user_facts 表 (结构化精确查询)    │ 持久  │
│           │ 用户偏好/确认事项/状态字段               │      │
└─────────────────────────────────────────────────────────┘
```

### 3.1 各层职责

| 层 | 载体 | 生命周期 | 访问方式 | DeepRAG 实现 |
|----|------|---------|---------|-------------|
| 感知记忆 | 当次输入 | 单次调用 | 即时 | `state["question"]`（已有） |
| 短期记忆 | messages 工作台 | 一次任务 | 直接读 | AgentState + 窗口/摘要治理 |
| 长期记忆 | 向量库 | 持久 | 语义检索 | Qdrant `agent_memory` + BGE-M3 |
| 实体记忆 | 结构化存储 | 持久 | 精确查询 | MySQL `user_facts` |

---

## 四、存储设计

### 4.1 长期记忆 — Qdrant `agent_memory` collection

复用现有 QdrantWriter 模式，新建独立 collection（与知识库 `chunks` 隔离）：

```python
# 新 collection: agent_memory
vectors_config = {"dense": VectorParams(size=1024, distance=Distance.COSINE)}
# 复用 BGE-M3 (1024维), 不引第二个嵌入模型

# Point payload 结构
{
    "user_id": "user-uuid",          # 记忆隔离(按 user_id, 非会话)
    "memory_type": "episodic",       # episodic / semantic / procedural
    "content": "用户曾要求所有代码用TypeScript",  # 记忆原文
    "summary": "偏好TypeScript",     # 压缩后的摘要(可选)
    "created_at": "2026-08-08T...",
    "last_accessed": "2026-08-08T...",# 新鲜度权重依据
    "access_count": 3,               # 被检索命中次数(重要性依据)
    "importance": 0.8,               # 重要性分数 [0,1]
    "valid_until": null,             # 有效期(可空=永久); Zep式时间感知
    "source_conv": "conv-uuid"       # 来源会话(溯源)
}
```

**检索方式**：dense 向量检索（query embedding → cosine 相似度），按 `user_id` + `memory_type` 过滤。

### 4.2 实体记忆 — MySQL `user_facts` 表

结构化偏好/状态，精确查询，不走语义检索：

```sql
CREATE TABLE user_facts (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id VARCHAR(50) NOT NULL,
    fact_key VARCHAR(100) NOT NULL,     -- 如 preferred_language / budget / role
    fact_value TEXT NOT NULL,            -- 如 "TypeScript" / "50000"
    confidence FLOAT DEFAULT 1.0,        -- 置信度
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_user_fact (user_id, fact_key),  -- 同key覆盖更新(冲突消解)
    INDEX idx_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

**访问方式**：`SELECT fact_key, fact_value FROM user_facts WHERE user_id=?`（每次会话开始一次性加载全部）。

### 4.3 短期记忆 — 治理 AgentState

不新建存储，治理现有的 `conversation_history`：

```
原始全量历史 → [摘要层] + [近N轮原文层]
              ↑ 旧的压缩    ↑ 最近保持完整
```

### 4.4 存储映射总表（回答「怎么存」）

| 信息类型 | 存储介质 | 理由 |
|---------|---------|------|
| 当前对话 messages | graph AgentState | 工作台，直接读 |
| 对话持久化备份 | MySQL `conversation_messages`（已有） | 历史回溯、前端展示 |
| 长期语义/情节记忆 | Qdrant `agent_memory`（新） | 需语义检索 |
| 结构化用户偏好/状态 | MySQL `user_facts`（新） | 需精确查询 |
| 热会话加速 | Redis（已有 `src/memory`） | 毫秒级访问热数据 |

---

## 五、三个工程问题的解答

### 5.1 存什么？（价值过滤）

**判断标准**：这条信息，下次任务开始时如果知道，会让 Agent 做得更好吗？

| ✅ 值得存 | ❌ 不值得存 |
|----------|-----------|
| 用户偏好（语言/风格/技术栈） | 中间推理过程 |
| 关键结论与决策（确认的方案） | 工具返回的原始日志 |
| 外部知识（FAQ/历史案例） | 闲聊内容 |
| 用户身份实体（角色/公司） | 失败的尝试细节 |

**过滤时机**：任务结束后（写阶段），由 LLM 判断 `is_important`，规则兜底。

### 5.2 怎么存？（混合存储 + 粒度）

混合存储见 §4.4。**粒度**是关键：

| 粒度 | 问题 | DeepRAG 选择 |
|------|------|-------------|
| 太细（每句一条） | 碎片化，检索拿不全 | ❌ 不取 |
| 太粗（整次对话一条） | 噪音大，命中也难用 | ❌ 不取 |
| **一次完整交互 / 一个知识点** | 信息完整、信噪比好 | ✅ 取这个 |

**落地**：长期记忆以「一轮问答的用户意图+结论」为一条；实体记忆以「一个独立事实」为一条。

### 5.3 什么时候取？（主动 + 被动结合）

| 策略 | 时机 | DeepRAG 实现 |
|------|------|-------------|
| **主动检索** | 任务开始前 | `_entry_node`：用 question 检索长期记忆 + 加载实体记忆 → 注入 system |
| **被动触发** | 执行中按需 | 封装 `recall_memory` 为 Tool，Agent 自主调用（P2） |

---

## 六、记忆压缩策略（回答 context 爆满）

落地四种方法到 DeepRAG：

### 6.1 方法对照

| 方法 | 解决维度 | DeepRAG 落地 | 阶段 |
|------|---------|-------------|------|
| **滑动窗口** | 历史太长怎么截 | conversation_history 保留近 N 轮 | P0 |
| **摘要压缩** | 截之前先提炼 | 旧历史 LLM 压成摘要，拼进 prompt | P0 |
| **重要性过滤** | 内容不等价怎么挑 | 规则+LLM 打分，低分不注入 | P1 |
| **结构化抽取** | 换更高效载体 | 抽成 user_facts 字段，直接注入 | P2 |
| Prompt Caching | 计算层优化 | （未来接入，与压缩互补） | P3 |

### 6.2 工程组合：滑动窗口 + 摘要（P0 主方案）

```
对话历史 = [长期摘要] + [近 N 轮原文]

构造 prompt 时:
  - 近 N 轮(如最近5轮): 保持原文, 与当前任务最相关
  - 第 N+1 轮之前的: 压成一段摘要, 只保留关键决策
  - N 由 token 预算动态决定(而非固定轮数)
```

### 6.3 层级式摘要（P1 进阶）

```
[近10轮原文] + [中期摘要(10~50轮)] + [长期摘要(50轮+)]
```

类比会议纪要：今天的逐条记，上月的记要点，去年的只记关键决策。

---

## 七、读-用-写闭环（接入 graph.py）

这是设计的核心，明确记忆如何接入现有 CRAG 状态机。

### 7.1 数据流总览

```
用户请求
   │
   ▼
┌──────────────────────────────────────────────┐
│ 读阶段 (_entry_node 增强)                      │
│  1. 加载实体记忆: SELECT user_facts           │
│  2. 主动检索长期记忆: Qdrant.query(question)   │
│  3. 拼入 system_prompt 作为背景                │
└──────────────────────────────────────────────┘
   │
   ▼
┌──────────────────────────────────────────────┐
│ 用阶段 (现有 retrieve→grade→generate→verify)   │
│  - conversation_history 全程工作(已治理窗口)   │
│  - P2: Agent 可调 recall_memory Tool 按需检索  │
└──────────────────────────────────────────────┘
   │
   ▼
┌──────────────────────────────────────────────┐
│ 写阶段 (新增 _memory_write_node, END 前)       │
│  1. 判断本次是否产生有价值信息 (LLM/规则)       │
│  2. 抽取实体 → 写 user_facts                   │
│  3. 摘要结论 → embed → 写 Qdrant agent_memory  │
│  4. 触发记忆整合(去重/冲突, 异步)              │
└──────────────────────────────────────────────┘
```

### 7.2 接入点伪代码

**读阶段 — 改造 `_entry_node`（graph.py）**：

```python
async def _entry_node(state: AgentState) -> Command:
    question = state["question"]
    user_id = state.get("user_id", "default")

    # === 新增: 读记忆 ===
    memory_ctx = await load_memory_context(user_id, question)
    # memory_ctx = {
    #   "user_facts": {"preferred_language": "Python", ...},   # 实体记忆
    #   "long_term": ["用户偏好简洁代码风格", ...]              # 长期记忆(语义检索top3)
    # }
    # → 注入到后续 prompt 的 system 段

    ...  # 原有 mode 分发逻辑不变
```

**写阶段 — 新增 `_memory_write_node`**：

```python
async def _memory_write_node(state: AgentState) -> dict:
    """任务结束写回记忆。source 标注是否含记忆支撑。"""
    user_id = state.get("user_id", "default")
    question = state["question"]
    answer = state.get("generation", "")

    # 1. 抽取实体事实 → user_facts (结构化抽取压缩法)
    facts = await extract_facts(question, answer)
    for k, v in facts.items():
        await upsert_user_fact(user_id, k, v)  # UNIQUE KEY 自动冲突消解

    # 2. 判断是否值得存长期记忆
    if await is_memorable(question, answer):
        summary = await summarize_interaction(question, answer)  # 粒度=一次交互
        embedding = await embed_query(summary)                   # 复用 BGE-M3
        await save_long_term_memory(
            user_id=user_id, content=summary, embedding=embedding,
            memory_type="episodic", source_conv=state["conversation_id"]
        )

    return {}  # 不改 state, 仅副作用
```

**接入 graph 边**：所有终止路径（generate→verify→END、tool_agent→END、plan_agent→END）在 END 前汇聚到 `_memory_write_node`。

### 7.3 source 字段增强

诚实标注记忆支撑，前端徽章区分：

```python
# 现有 source 扩展
"kb_grounded"      # 知识库引用
"kb_grounded+mem"  # 知识库 + 记忆支撑(新增)
"mem"              # 仅记忆支撑(新增)
"non_kb" / "tool" / "planned" / "refused"  # 不变
```

---

## 八、记忆整合（防止越积越乱）

### 8.1 三个环节

| 环节 | 做法 | DeepRAG 实现 | 频率 |
|------|------|-------------|------|
| **去重** | 语义相近的合并 | Qdrant 检索相似度>0.92 则合并 | 每次写入时 |
| **冲突消解** | 矛盾的留新的 | `user_facts` UNIQUE KEY 覆盖；长期记忆标 valid_until | 写入时 |
| **抽象提炼** | 情节→语义 | LLM 把一批情节记忆总结成规律 | 每日异步任务 |

### 8.2 记忆衰减（Zep 式时间感知）

```python
# 检索排序分数 = 语义相似度 × 时间新鲜度权重
final_score = cosine_similarity(query, mem) * freshness_weight(mem)

# freshness_weight = exp(-λ × 天数)  # λ=0.05, 约2周半衰减到一半
# access_count 高的记忆衰减更慢(常用=重要)
```

定期（每日）LLM 审查长期记忆库，把 `valid_until` 过期或与现状矛盾的记忆标记失效。

---

## 九、分阶段实施路线

### P0 — 短期记忆治理（治最痛的失忆问题）

**目标**：CRAG 多轮对话不再爆 token。
**改动**：仅 graph.py + 复用 SummarizationMiddleware 思路，**不动存储**。

- [ ] `conversation_history` 加滑动窗口（token 预算驱动，非固定轮数）
- [ ] 旧历史 LLM 摘要压缩，拼进 prompt
- [ ] 修复 checkpoint：thread_id 复用 conversation_id（而非每请求新 uuid）

**验证**：长对话（>10轮）不超 token 上限，且关键早期信息不丢。

### P1 — 长期记忆 + 读闭环（跨会话积累）

**目标**：Agent 能「想起」历史经验和用户偏好。
**改动**：Qdrant 新 collection + graph.py `_entry_node` 增强读。

- [ ] 创建 `agent_memory` collection（复用 BGE-M3）
- [ ] `_entry_node` 加主动检索 + 实体记忆加载
- [ ] 检索结果注入 system prompt
- [ ] 改造现有 `src/memory/retriever.py`：换掉 SentenceBERT，用 BGE-M3

**验证**：用户跨会话表达偏好后，新会话能体现该偏好。

### P2 — 写闭环 + 压缩（沉淀 + 主动压缩）

**目标**：任务结束自动沉淀记忆；工具结果主动压缩。

- [ ] 新增 `_memory_write_node`（图终止前汇聚）
- [ ] 事实抽取 → `user_facts` 表（结构化压缩）
- [ ] `user_facts` DDL 加入 lifespan `ensure_schema`
- [ ] 工具结果主动压缩（ReAct 链路）
- [ ] 封装 `recall_memory` 为 Tool（被动触发）

**验证**：对话产生偏好后，能写入并影响后续检索排序。

### P3 — 记忆整合 + 高级特性

**目标**：记忆库长期保持健康；程序记忆。

- [ ] 每日异步任务：情节→语义抽象提炼
- [ ] 记忆衰减 + 过期清理
- [ ] 程序记忆（可复用 SOP，按需，YAGNI 评估后再做）
- [ ] 知识图谱（实体-关系，多跳推理，远期）

---

## 十、对现有 `src/memory/` 模块的处置

现有模块设计方向正确，但未接入且有瑕疵，按本方案改造：

| 现有组件 | 问题 | 改造方向 |
|---------|------|---------|
| `ConversationMemory` | 引第二张 conversation_messages 表（DDL 缺失） | 改为复用现有表 + Redis 加速；或 P0 阶段直接不用（AgentState 治理够用） |
| `MemoryRetriever` | 用 SentenceBERT（384维，与系统不兼容） | **换 BGE-M3**，对接 Qdrant `agent_memory` |
| `UserPreferenceLearner` | redis_client/mysql 注入为 None | 对接 lifespan 共享实例；拆出实体写入到 `user_facts` |

---

## 十一、风险与取舍

| 风险 | 取舍 | 决策 |
|------|------|------|
| 记忆检索注入增加延迟 | 多一次 Qdrant 查询 + embed | 读阶段并行化，控制在 +200ms |
| 记忆噪音降低检索质量 | 存太多信噪比差 | 严格「存什么」过滤 + 衰减 |
| 摘要丢失关键细节 | 不可逆信息损失 | P0 先做，P1 上层级摘要缓解 |
| 写记忆的 LLM 调用成本 | 每次任务结束多一次 LLM | 异步写入，不阻塞响应 |
| 跨会话记忆的隐私 | 用户数据隔离 | 严格按 user_id 隔离，删除会话级联清记忆 |

---

## 十二、与理论框架的映射（方案完整性自检）

| 理论要素 | 本方案落地 | 章节 |
|---------|-----------|------|
| 四层记忆 | 感知/短期/长期/实体 全覆盖 | §3 |
| 存什么 | 价值过滤表 | §5.1 |
| 怎么存 | 混合存储 + 粒度 | §4.4, §5.2 |
| 什么时候取 | 主动检索 + 被动触发 | §5.3 |
| 读-用-写闭环 | graph.py 接入点 | §7 |
| 滑动窗口 | token 预算窗口 | §6.2 (P0) |
| 摘要压缩 | 旧历史 LLM 摘要 | §6.2 (P0) |
| 重要性过滤 | 规则+LLM 打分 | §6.1 (P1) |
| 结构化抽取 | user_facts 字段 | §6.1 (P2) |
| 层级摘要 | 近/中/远三层 | §6.3 (P1) |
| 记忆整合 | 去重/冲突/抽象 | §8 |
| 记忆衰减 | 时间感知权重 | §8.2 |
| Prompt Caching | 计算层互补 | §6.1 (P3) |
| Mem0/Letta/Zep 思想 | 借鉴（独立服务层/三级/时间感知），自研实现 | 全文 |

---

## 总结

本方案以**四层记忆模型**为骨架，以**读-用-写闭环**为运行机制，以**四种压缩方法**控制 context，全部**复用 DeepRAG 现有存储栈**（Qdrant + MySQL + Redis + BGE-M3），分 P0→P3 四阶段渐进落地。

**立即可做且最划算的是 P0**：仅改 graph.py，治好「对话变长就失忆」这个真实痛点，不动存储、风险最低。需要我现在开始实施 P0 吗？

# DeepRAG 模式驱动路由重构设计（mode-routing）

- **日期**: 2026-08-04
- **状态**: 待评审
- **范围**: 将"全局 LLM 意图识别路由（route 节点）"重构为"用户显式选择模式（mode）驱动的入口分发"
- **前置**: 意图重构（2026-08-04-intent-refactor-stage1）已完成；本设计取代其路由层

---

## 1. 背景与动机

上一轮意图重构将 8 类互斥 intent 压缩为 4 route + 正交字段。验证中发现 **`needs_planning`（agent 时判断是否含 KB）存在固有局限**：router 不知知识库内容，"简历技术栈 + 算"这类**简短多源问题**会被误判为 `needs_planning=false` → 走 tool_agent（不检索 KB）→ **漏 KB 答案**。根因与 "kb vs open_qa 预测" 是同一难题：**让 LLM 在不知 KB 内容、不知用户意图深度的情况下做全局路径预测，不可靠**。

**解决思路**：把"走哪条执行路径"从"LLM 预测"改为"用户显式选择"。一个不可靠的预测问题，变成一个可靠的输入。这是主流产品（ChatGPT 模式切换、Perplexity Focus）的成熟做法。

## 2. 目标 / 非目标

**目标：**
- 用 `mode` 字段（用户选）替代 `route`（LLM 判）作为一级分发依据
- 三模式：`quick`（快速问答）/ `kb`（知识库问答）/ `agent`（智能体），默认 `quick`
- 最大化复用现有已验证的三套执行逻辑（CRAG 全套、tool_agent ReAct、plan_agent 多源编排）
- 删除全局意图识别（`ROUTER_PROMPT`），降低复杂度与不可靠预测

**非目标（明确不在本次范围）：**
- LangChain `create_react_agent` + middleware 迁移（独立后续阶段，见 §11）
- safety 独立模块化（当前用规则前置 + 模式内护栏兜底，后续单独优化）
- 模式间自动升级 / "建议切换"提示
- kb 模式多跳检索（`run_search_agent` 函数保留但本次不在主路径调用）
- 记忆层（LangGraph Store）

## 3. 设计概览

三模式本质是**严谨度/延迟权衡**，不是"能做什么"的区分：

| 模式 | 执行逻辑 | 特点 | 对应现有实现 |
|---|---|---|---|
| **quick**（快速问答，默认） | 轻量 ReAct（含 KB 检索工具），LLM 自主选工具，**不做 grade/verify 自纠** | 快 | `run_tool_agent`（扩展含 KB 工具） |
| **kb**（知识库问答） | 全套 CRAG（retrieve→grade→generate→verify→rewrite 自纠） | 准 | 现有 CRAG 节点链 |
| **agent**（智能体） | 入口轻量 LLM 判复杂度：简单→ReAct（同 quick）；复杂→plan 规划 + 执行 | 全 | `run_tool_agent` + `run_plan_agent` |

**三模式都能查 KB**——区别在 quick 查了不做自纠（快）、kb 强制自纠（准）、agent 加规划（全）。

**执行路径图：**

```
用户选 mode (默认 quick)
    │
   entry〔规则:闲聊/能力→reply_direct | 规则:明显越界→END〕  ← 0 次 LLM
    │
    ├─ quick ───────────────→ tool_agent〔ReAct: KB检索+web+天气+时间+calc〕→ END
    │                           LLM 自主选工具, 不做 grade/verify
    │
    ├─ kb ──→ retrieve → grade ─→ generate → verify → END  (全套 CRAG 自纠)
    │             ↑          ↓ 无相关
    │             └─ rewrite ←└→ tool_agent (web_search 兜底)
    │
    └─ agent → 轻量 LLM 判复杂度
                 ├─ 简单 → tool_agent (同 quick 的 ReAct) → END
                 └─ 复杂 → plan_agent (规划 + 执行 + 综合) → END
```

## 4. 详细设计

### 4.1 mode 字段

- 取值：`"quick"` | `"kb"` | `"agent"`
- 默认：`"quick"`
- 传递链：前端选择器 → `QueryRequest.mode` → 初始 `AgentState.mode` → `_entry_node` 读取分发
- 粒度：**per-message**（每条消息携带当前 mode）。切模式不新建会话，多轮历史跨模式保留。

### 4.2 `_entry_node`（替代 `_route_node`）

纯分发 + 规则快通道，**不调用 LLM 做意图识别**：

```python
async def _entry_node(state: AgentState) -> Command:
    question = state["question"]
    mode = state.get("mode", "quick")
    # ① 规则快通道: 明显闲聊/能力问询 → reply_direct (跨模式, 0 LLM)
    if _classify_by_rule(question) == "direct":
        return Command(update={"mode": mode, "intent": "chitchat", "source": ""}, goto="reply_direct")
    # ② safety 规则前置: 明显越界 → END (0 LLM)
    if _safety_rule(question) == "refuse":
        return Command(update={"mode": mode, "source": "refused",
                               "generation": "抱歉，这类问题我无法回答。"}, goto=END)
    # ③ 按 mode 分发
    if mode == "kb":
        return Command(update={"mode": mode}, goto="retrieve")
    if mode == "agent":
        if await _judge_complex(question):  # 轻量 LLM 判复杂度
            return Command(update={"mode": mode}, goto="plan_agent")
        return Command(update={"mode": mode}, goto="tool_agent")
    # quick (默认)
    return Command(update={"mode": mode}, goto="tool_agent")
```

### 4.3 safety 策略（规则前置 + 模式内护栏）

本次不做独立 safety LLM（避免每请求 +1 次 LLM 拖慢 quick）：

- **`_safety_rule(question)`**：正则关键词匹配明显越界（攻击脚本/漏洞利用/黑客/炸弹/违禁品/隐私窃取等），命中→`refuse`→END。0 LLM。
- **兜底**：模糊越界由各模式系统提示护栏处理（`TOOL_AGENT_PROMPT` / `GENERATOR_PROMPT` 加安全护栏，违反时回复固定拒答）。
- **后续**：safety 独立模块化（规则库 + 可选 LLM 复核）作为独立优化，见 §11。

### 4.4 quick 与 agent常态 共用增强 `tool_agent`

quick 和 agent 的简单路径都走同一个 ReAct，工具集相同：

- **`run_tool_agent` 扩展签名**：新增 `retriever, tenant_id, kb_ids` 参数（当前只接收 question/history）
- **工具集**：`search_knowledge_base` + `get_weather` + `get_time` + `web_search` + `calculate`（LLM 自主选用）
- **`_dispatch_tool` 加 KB 分支**：`search_knowledge_base` → `retriever.retrieve(query, filter) + rerank`，返回 top3 摘要
- **`TOOL_AGENT_PROMPT` 微调**：增加"问题涉及知识库事实时调 `search_knowledge_base`；实时/外部信息调对应工具；无需工具则常识作答"
- **source 判定**：调用了任意工具（含 KB）→`"tool"`；未调工具纯常识→`"fallback_unverified"`

### 4.5 kb 模式（全套 CRAG，简化）

- `_retrieve_node` **固定走 simple 路径**（单次检索 + rerank），删除 complex 分支调用。kb 模式核心价值是 grade/verify 自纠，单次检索 + rerank 足够；多跳需求由 agent 的 plan 覆盖。
- grade 无相关 → 保留 `goto="tool_agent"`（web_search 兜底，沿用"检索无果结合 web search"决策）。
- 删除 `complexity` 字段依赖（kb 不再需要）。

### 4.6 agent 模式复杂度判断（`_judge_complex`）

- 轻量 LLM 判断（`AGENT_COMPLEX_JUDGE_PROMPT`）：问题是否需要多信息源/多步骤/组合（如"查简历 X + 算 Y"、"对比 A + 天气 B"）→ `{complex: true/false}`
- **失败兜底 `false`**（→ tool_agent ReAct，更轻量，agent 模式下误判成本可控：用户已选 agent，简单问题走 ReAct 完全可接受）
- 仅 agent 模式触发（quick/kb 不调用）

### 4.7 新增 `get_time` 工具

- 纯本地实现（`datetime`，无外部 API），返回当前东八区时间字符串：`"2026-08-04 14:30:00 Monday"`
- 新增 `TIME_TOOL` 声明，加入 `EXTERNAL_TOOLS`，`_dispatch_tool` 加 `get_time` 分支

## 5. 契约变更

### 5.1 API（`apps/api/query.py`）

- `QueryRequest` 新增字段：`mode: str = "quick"`
- `/query` 与 `/query/stream` 初始 state 增加 `"mode": req.mode`；删除 `"route"`、`"needs_planning"` 初值
- `_step_detail`：`route` 分支重命名为 `entry`，展示"模式: quick/... + 子路径"

### 5.2 AgentState（`src/agents/graph.py`）

| 字段 | 操作 |
|---|---|
| `mode` | **新增** `str` |
| `route` | **删除**（不再用于分发） |
| `needs_planning` | **删除**（移入 `_judge_complex` 局部，不入 state） |
| `complexity` | **删除**（kb 固定 simple，不再需要） |
| `safety` | 保留（`_safety_rule` 结果） |
| `intent`/`subtype` | 保留作统计/展示镜像（不再驱动路由） |
| `source` | 保留（前端徽章），值域不变 |

### 5.3 前端（`web/.../Chat.tsx`）

- 新增模式选择器（segmented control，3 项：快速问答 / 知识库 / 智能体），默认"快速问答"
- 用 zustand persist 记忆用户选择（key: `chat-mode`）
- 请求 body 增加 `mode` 字段
- **badge / 思考过程展示不动**：`source` 值域与 `thought`/`search`/`tool_call`/`plan` SSE 事件不变

## 6. 文件改动清单

| 文件 | 操作 |
|---|---|
| `src/agents/graph.py` | `_route_node`→`_entry_node`（mode 分发 + 规则闲聊 + 规则 safety）；新增 `_safety_rule`、`_judge_complex`；`_tool_agent_node` 改为闭包捕获 retriever；`_retrieve_node` 删 complex 分支；`AgentState` 加 `mode` 删 `route`/`needs_planning`/`complexity`；节点名 `route`→`entry` |
| `src/agents/tools.py` | 新增 `get_time` + `TIME_TOOL`；新增 `REACT_TOOLS` 工具集（`SEARCH_TOOL`+`WEATHER`+`CALC`+`WEB_SEARCH`+`TIME`，供 quick/agent常态 用，**取代** `EXTERNAL_TOOLS`）；扩展 `run_tool_agent`（接收 retriever/tenant_id/kb_ids，用 `REACT_TOOLS`）；`_dispatch_tool` 加 `search_knowledge_base`/`get_time` 分支 |
| `src/agents/prompts.py` | 删 `ROUTER_PROMPT`、`OPEN_ANSWER_PROMPT`；新增 `AGENT_COMPLEX_JUDGE_PROMPT`；微调 `TOOL_AGENT_PROMPT`（鼓励查 KB）；保留 `OPEN_ANSWER_FALLBACK_PROMPT`/`PLAN_PROMPT`/CRAG 各 prompt |
| `apps/api/query.py` | `QueryRequest` 加 `mode`；初始 state 加 `mode` 删 `route`/`needs_planning`；`_step_detail` route→entry |
| `web/.../Chat.tsx` | 模式选择器 + zustand persist + 请求带 mode |
| `scripts/test_modes.py` | **新增**，替代 `test_intent_refactor.py` |
| `scripts/test_tool_calling.py` | 更新：以 quick 模式发请求 |
| `scripts/test_plan_agent.py` | 更新：以 agent 模式发请求 |

**保留不动**：CRAG 的 grade/generate/verify/rewrite 节点逻辑、`run_plan_agent`、`run_search_agent`（函数保留，主路径暂不调用）、`_classify_by_rule`、所有工具实现（weather/calc/web_search）。

## 7. 删除清单

- `ROUTER_PROMPT`（全局意图识别 prompt）
- `OPEN_ANSWER_PROMPT`（库外常识直答，已废弃）
- `_route_node` 函数（被 `_entry_node` 取代）
- AgentState 的 `route` / `needs_planning` / `complexity` 字段
- `_retrieve_node` 内 complex 分支（kb 固定 simple）
- `EXTERNAL_TOOLS` 常量（被 `REACT_TOOLS` 取代）
- `scripts/test_intent_refactor.py`（被 `test_modes.py` 取代）

## 8. 兼容性与回归风险

- **source 值域不变**：前端徽章逻辑零改动。
- **SSE 事件不变**：`thought`/`search`/`tool_call`/`plan`/`token`/`step`/`done` 全部保留。唯一变化：`step` 事件中 `node="route"` → `node="entry"`。
- **多轮历史不变**：`conversation_history` 跨模式保留，切模式不断会话。
- **风险点**：quick 模式下 LLM 自主决定查不查 KB，若 LLM 倾向不查 KB 而用常识，对 KB 事实问题可能不如 kb 模式严谨——这是 quick 的设计权衡（求快），通过 `TOOL_AGENT_PROMPT` 引导缓解。
- **回退**：`run_search_agent` 保留，后续若要恢复 kb 多跳可直接接回。

## 9. 验证计划

新增 `scripts/test_modes.py`，覆盖三模式分发（沿用项目 `scripts/test_*` 脚本式断言风格）：

| 用例 | 期望 |
|---|---|
| quick + "北京今天天气" | source=tool，出现 tool_call 事件，无 retrieve step |
| quick + KB 事实问题（如"简历里掌握哪些技术栈"） | source=tool，出现 search 事件（LLM 调了 KB 工具） |
| kb + KB 事实问题 | 出现 retrieve→grade→generate→verify step，source=kb_grounded（或 fallback） |
| agent + 简单（"北京天气"） | source=tool（走 tool_agent） |
| agent + 复杂多源（"简历技术栈 + 算 365×3÷7"） | source=planned（走 plan_agent） |
| 任模式 + "你好" | source=non_kb（规则快通道→reply_direct） |
| 任模式 + "帮我写攻击网站的脚本" | source=refused（规则 safety→END） |

回归：`test_tool_calling.py`（quick 模式）、`test_plan_agent.py`（agent 模式）全过。

## 10. 设计决策记录（本次确认）

1. **quick 含 KB 检索**（轻量 ReAct，LLM 自主决定查不查，不做自纠）——quick 也能答 KB 问题，与 kb 的差别是严谨度
2. **默认模式 quick**
3. **agent 内复杂度用轻量 LLM 判断**（非统一 plan），贴合"常态 ReAct + 复杂 plan"
4. **safety 规则前置 + 模式内护栏**（0 额外 LLM，保 quick 快），后续独立优化
5. **mode 是 per-message**，切模式不新建会话
6. **kb 固定 simple 检索**（删 complex 分支），多跳由 agent plan 覆盖
7. **quick 与 agent 常态共用增强 tool_agent**（工具集 = KB + web + 天气 + 时间 + calc）

## 11. 后续阶段（不在本次范围）

- **阶段 A（LangChain 迁移）**：ReAct 层（`run_tool_agent`）迁到 `langgraph.prebuilt.create_react_agent` + `langchain_openai.ChatOpenAI`；引入 1–2 个 middleware（消息汇总、工具审计接 langfuse）。CRAG 层与 plan_agent 保持手写。
- **阶段 B（safety 模块化）**：规则库 + 可选 LLM 复核的独立安全模块。
- **阶段 C（评估）**：ragas 接入量化三模式质量/延迟。

# 意图识别重构（执行路由压缩 + 正交字段 + 检索结果决定来源）

> 状态：已与用户确认设计方向（brainstorming），待 spec 审阅 → writing-plans。
> 日期：2026-08-04
> 关联：用户诊断"8 类混合 4 维度"+"kb/open_qa 不应由路由器预判"+"检索无果应结合工具"。本 spec 合并原阶段1+2。

## 1. 背景与问题

当前 8 类单层互斥意图混合 4 维度（用户意图/信息来源/执行策略/安全），边界重叠：
- chitchat/capability 处理相同（冗余）
- kb_simple/kb_complex 已由 complexity 承载（重复编码）
- tool_call/multi_source 边界模糊
- out_of_scope 是安全判定，不该与问答互斥
- **router 预判 kb vs open_qa（猜 KB 有没有答案）不稳**——"什么是RAG"走 kb 还是 open_qa 取决于 KB 内容，不取决于问题
- **检索无果只靠 LLM 常识不够精准**，应结合 web_search 等工具

## 2. 已确认决策（用户拍板）

| 决策点 | 选择 |
|---|---|
| 执行路由 | 压缩为 **4 条**：`direct`/`retrieve`/`agent`/`refuse`（**砍掉 open_answer**） |
| kb/open_qa | **不预判**，统一走 retrieve 先检索，由 grade（检索结果）决定来源 |
| 检索无果兜底 | goto **tool_agent**（web_search 联网精准化 + 常识，auto 自决） |
| 正交字段 | router 输出 `route + needs_planning + complexity + safety + subtype` |
| safety | 独立维度，前置检查（不与 route 互斥） |
| agent 入口 | plan/ReAct 都保留，router 第一层判 `needs_planning` 分流 plan_agent / tool_agent |
| open_answer 节点 | **删除**（route 不再 goto；tool_agent 内部已有常识回退） |
| subtype | 原细粒度降级统计（不参与路由） |

## 3. 目标 / 非目标

**目标**
1. router 输出正交字段（route 4 选 1 + needs_planning + complexity + safety + subtype）。
2. 合并冗余：chitchat+capability→direct，kb_simple+kb_complex→retrieve（complexity 分流）。
3. **open_qa 并入 retrieve**（先检索），消除 router 预判 kb/open_qa。
4. safety 前置（独立维度）。
5. agent 按 needs_planning 分流 plan_agent / tool_agent（两者保留）。
6. 删 open_answer 节点；检索无果→tool_agent（web_search+常识）。

**非目标（阶段 3）**
- confidence 驱动主动澄清

## 4. router 新输出格式

```json
{
  "route": "direct | retrieve | agent | refuse",
  "needs_planning": false,
  "complexity": "simple",
  "safety": "allow",
  "subtype": "kb_simple",
  "sub_queries": []
}
```

- `route`：4 条路径之一（**唯一参与路由**）
- `needs_planning`：bool，仅 route=agent 时（true→plan_agent 多源/含KB，false→tool_agent 纯工具 ReAct）
- `complexity`：simple/complex，仅 route=retrieve 时（分流单次/ReAct 检索）
- `safety`：allow/refuse，**独立维度**，refuse→END（优先于 route）
- `subtype`：原 8 类之一（统计用，不参与路由）
- `sub_queries`：retrieve+complex 时给 2-3 个

## 5. ROUTER_PROMPT 重构（`prompts.py`）

```
判断用户问题的处理路径与属性。

问题: {question}

route(执行路径, 4选1):
- direct: 闲聊/问候/能力问询, 无需检索直接回复 (例: "你好"、"你能做什么")
- retrieve: 面向知识的问题(无论是知识库事实还是通用概念, 都先检索知识库, 由检索结果决定来源) (例: "X是什么"、"对比A和B"、"什么是RAG")
- agent: 需要工具(天气/计算/搜索)或多源组合(KB+工具) (例: "北京天气"、"简历技术栈+算工作日")
- refuse: 越界/有害/无关指令 (例: 攻击脚本、医疗诊断)

needs_planning(仅agent时判, 其余false): 多源组合(含KB+工具)时true, 纯工具(天气/计算/搜索)时false
complexity(仅retrieve时判, 其余simple): 多跳/对比给complex, 单事实/simple问题给simple
safety(独立): allow; 越界/有害/违法/隐私/实时专业建议(医疗法律投资)给refuse
subtype(统计用, 原细粒度): chitchat/capability/kb_simple/kb_complex/open_qa/tool_call/multi_source/out_of_scope 之一

只输出JSON: {{"route":"...", "needs_planning":false, "complexity":"simple", "safety":"allow", "subtype":"...", "sub_queries":[]}}
(sub_queries仅retrieve+complex时给2-3个, 其余空数组)
```

> 关键：route=retrieve **不再区分 kb/open_qa**——通用概念（"什么是RAG"）也走 retrieve，由 grade 检索结果决定。

## 6. route 节点新逻辑（`graph.py` `_route_node`）

```python
async def _route_node(state: AgentState) -> Command:
    question = state["question"]
    # ① 规则快通道: 明显闲聊/能力 → direct(省 LLM)
    if _classify_by_rule(question) == "direct":
        return Command(update={"route": "direct", "subtype": "chitchat", "intent": "chitchat"}, goto="reply_direct")
    # ② LLM 判正交字段
    try:
        res = await chat_json([{"role": "user", "content": ROUTER_PROMPT.format(question=question)}])
        route = res.get("route", "retrieve")
        needs_planning = bool(res.get("needs_planning", False))
        complexity = res.get("complexity", "simple")
        safety = res.get("safety", "allow")
        subtype = res.get("subtype", route)
        sub_queries = [q for q in res.get("sub_queries", []) if q][:3]
    except Exception:
        route, needs_planning, complexity, safety, subtype, sub_queries = "retrieve", False, "simple", "allow", "kb_simple", []
    base = {"route": route, "needs_planning": needs_planning, "complexity": complexity,
            "safety": safety, "subtype": subtype, "intent": subtype, "sub_queries": sub_queries}
    # ③ safety 前置(独立维度, 优先于 route)
    if safety == "refuse":
        return Command(update={**base, "source": "refused",
                               "generation": "抱歉，这类问题我无法回答。我可以帮你检索知识库内容，或回答通用常识问题。"}, goto=END)
    # ④ 按 route 分流
    if route == "direct":
        return Command(update=base, goto="reply_direct")
    if route == "agent":
        return Command(update=base, goto="plan_agent" if needs_planning else "tool_agent")
    if route == "refuse":
        return Command(update={**base, "source": "refused",
                               "generation": "抱歉，这类问题我无法回答。"}, goto=END)
    # retrieve(默认, 含原 kb + open_qa)
    return Command(update=base, goto="retrieve")
```

## 7. 其他改动

### 7.1 AgentState（`graph.py`）
新增：`route: str`、`needs_planning: bool`、`safety: str`。保留 `intent`（subtype 镜像，兼容/统计）、`complexity`、`sub_queries`、`source`。

### 7.2 `_classify_by_rule`（`graph.py`）
返 `"direct"`（原 chitchat/capacity）。规则不变，返回值统一 direct。`__main__` 自检同步更新（assert "你好"→"direct"）。

### 7.3 **删除 open_answer 节点**
- `_route_node` 不再有 open_answer 分支（上方已删）
- `build_graph` 删 `wf.add_node("open_answer", ...)` 和 `wf.add_edge("open_answer", END)`
- `_open_answer_node` 函数可删（或保留 unused；建议删）
- OPEN_ANSWER_PROMPT 保留（OPEN_ANSWER_FALLBACK_PROMPT 仍被 tool_agent 回退用）

### 7.4 grade 兜底（`_grade_node`）—— 已满足，无需改
当前 grade 无证据 → `goto="tool_agent"`（工具调用 v1 改的）。**这正满足新需求**：检索无果 → tool_agent（auto 模式：有 Tavily key 时 LLM 倾向 web_search 联网精准化，无 key 时常识答）。
- 配置提示：用户配 `TAVILY_API_KEY` 后，检索无果会主动 web_search，回答更精准（实现"结合工具"）。

### 7.5 query.py `_step_detail`
route 分支按 subtype 展示细粒度（中文映射沿用原 8 类）。删 open_answer 节点的 step 分支（if node=="open_answer"）。

### 7.6 前端
- **source 不变**（6 色）。
- STEP_ICON 沿用原映射（按 subtype），open_answer 图标可留可删。
- 初始 state 加 `route/needs_planning/safety` 默认值（query.py 的 ainvoke 初始 dict）。

## 8. 数据流（重构后）

```
question
 → 规则快通道(direct?) ─yes→ reply_direct → END (non_kb)
 → LLM 判 route+needs_planning+complexity+safety+subtype
 → safety==refuse? ─yes→ END (refused)
 → route分流:
     direct   → reply_direct → END (non_kb)
     retrieve → retrieve → grade
                    ├─ 有证据 → generate → verify → END (kb_grounded)
                    └─ 无证据 → tool_agent(web_search+常识) → END (tool/fallback)
     agent    → needs_planning? plan_agent : tool_agent → END (planned/tool)
     refuse   → END (refused)
```

> open_answer 路径消失：原 open_qa 问题（"什么是RAG"）→ retrieve → grade → 无证据 → tool_agent（web_search 精准化/常识）。

## 9. 测试

**`scripts/test_intent_refactor.py`**（新建）：
- direct: "你好" → route=direct（规则快通道）→ non_kb，无 retrieve
- retrieve simple: 简历内单事实 → route=retrieve, complexity=simple → kb_grounded
- retrieve complex: "对比简历里X和Y" → route=retrieve, complexity=complex + sub_queries
- **原 open_qa: "什么是HTTP协议" → route=retrieve（不再 open_answer）→ grade 无证据 → tool_agent → tool/fallback**（关键变化）
- agent 工具: "北京天气" → route=agent, needs_planning=false → tool_agent → tool
- agent 多源: "简历技术栈+算工作日" → route=agent, needs_planning=true → plan_agent → planned
- refuse: "写攻击脚本" → safety=refuse → refused

回归：test_tool_calling.py / test_plan_agent.py（路径未变，路由字段变了，可能要小改断言）。

## 10. 关键设计记录

- **4 route 取代 8 intent**：route（路径）+ needs_planning（agent 深度）+ complexity（retrieve 深度）+ safety（安全）+ subtype（统计），正交独立。
- **open_qa 并入 retrieve（核心）**：消除 router 预判 kb/open_qa。通用概念也先检索，由 grade 决定来源。代价：纯常识多一次检索（可接受，~100ms）。
- **检索无果→tool_agent（web_search 精准化）**：取代原"纯常识 open_answer"。配 Tavily key 后主动联网，回答更准；无 key 常识答。复用现有 grade 兜底（零改动）。
- **safety 前置（一次 LLM 独立字段）**：物理一次调用，逻辑独立维度。
- **agent 按 needs_planning 分流（plan/ReAct 都保留）**：判断在 router 第一层，无额外调用。
- **subtype 降级 + intent 兼容**：原 8 类保留统计，intent 字段镜像 subtype 减少改动。

## 11. 阶段 3 预告（后续，不在本次）

confidence 驱动主动澄清：router 输出 confidence，低置信度时主动追问而非猜测。

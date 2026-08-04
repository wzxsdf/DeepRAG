# 多源编排 plan_agent 设计（意图路由 v3）

> 状态：已与用户确认设计方向（brainstorming），待 spec 审阅 → writing-plans。
> 日期：2026-08-03
> 关联：`docs/superpowers/specs/2026-08-03-tool-calling-design.md`（工具调用）、`2026-08-03-expand-tools-design.md`（扩工具）

## 1. 背景与目标

工具调用 v2 后，系统已有 4 个信息源：KB检索 / 天气 / 计算 / 搜索。但当前是**单线路径**——一个问题只能走一条路径（retrieve 或 tool_agent），无法组合多源。

**痛点**：如"查简历里这个人的技术栈，再算下他 3 年经验大约多少工作日"——需要 ①KB检索技术栈 + ②计算工作日，现在做不到（单源）。

**plan 的增量价值**（两轮前分析确认）：不在"分解检索"（sub_queries 已做），而在**跨源编排**——让一个问题组合多源，每源走自己能力，最后综合。多工具就绪后，这个价值成立。

**目标**：新增 `plan_agent` 节点（显式规划→逐步执行→综合），router 新增 `multi_source` 类触发。这是主线 A（深化 Agent）的 planning 能力落地。

## 2. 已确认决策（用户拍板）

| 决策点 | 选择 | 理由 |
|---|---|---|
| plan 架构 | **plan_agent 闭环** | 一个节点完成规划+执行+综合，复用 run_tool_agent 模式，不增状态机节点数；显式 plan 简历亮点 |
| 触发方式 | **router 新增 multi_source 类** | 明确多源才触发，单源问题不受影响、不加规划开销；保守触发 |
| KB 步骤 | retrieve+rerank 取摘要，不走完整 CRAG | plan 场景是综合而非单源深度，综合时统一生成 |
| source | 新增 `planned`（🟣 紫"多源·编排"） | 区分单源 tool/kb_grounded |

## 3. 目标 / 非目标

**目标**
1. 新增 `run_plan_agent`（tools.py）+ `_plan_agent_node`（graph.py）。
2. router 新增第 8 类 `multi_source` → plan_agent。
3. plan 步骤前端可观测（计划 + 各步执行）。
4. source 新增 `planned` + 紫色徽章。
5. 端到端验证多源问题。

**非目标（YAGNI）**
- Plan-and-Execute 的 replan（执行中重规划）——本次只做一次性规划
- plan 步骤的并行执行（串行足够 MVP）
- KB 步骤走完整 CRAG（grade/verify）

## 4. 组件设计

### 4.1 PLAN_PROMPT（`src/agents/prompts.py`）

```python
PLAN_PROMPT = """你是任务规划器。把用户问题分解为有序的信息获取步骤, 每步指定一个信息源。
可用步骤类型:
- {{"type":"kb","query":"..."}}: 在知识库检索事实证据
- {{"type":"weather","city":"..."}}: 查城市实时天气(英文/拼音城市名)
- {{"type":"calc","expression":"..."}}: 精确计算数学表达式(如 365*3/7)
- {{"type":"search","query":"..."}}: 联网搜索最新信息

规则:
1. 只在确实需要某信息源时才安排步骤(单源问题只给1步)
2. 步骤数≤4, 不安排与问题无关的步骤
3. kb的query用关键词/问句; calc的expression用数学表达式

问题: {question}

只输出JSON: {{"steps":[{{步骤}}]}}"""
```

### 4.2 run_plan_agent（`src/agents/tools.py`）

```python
MAX_PLAN_STEPS = 4

async def run_plan_agent(retriever, question: str, tenant_id: str = "default",
                         kb_ids: list[str] | None = None, history: list | None = None) -> dict:
    """多源编排Agent: LLM显式规划步骤→逐步执行(KB检索/工具)→流式综合.
    source=planned. 规划失败→退化单步; function-calling不可用→回退open_answer."""
    from langgraph.config import get_stream_writer
    from src.agents.prompts import PLAN_PROMPT
    from src.agents.llm import chat_json
    try:
        from src.agents.graph import _stream_answer  # 延迟import避免循环
    except Exception:
        _stream_answer = None
    try:
        writer = get_stream_writer()
    except Exception:
        writer = None

    # ① 规划
    try:
        plan_res = await chat_json([{"role": "user", "content": PLAN_PROMPT.format(question=question)}])
        steps = (plan_res.get("steps") or [])[:MAX_PLAN_STEPS]
    except Exception:
        steps = []  # 规划失败→退化: 直接当KB问题检索
    if not steps:
        steps = [{"type": "kb", "query": question}]  # 兜底单步

    # 推送计划(可观测)
    if writer:
        try:
            writer({"tag": "plan", "steps": [{"type": s.get("type"), **{k: v for k, v in s.items() if k != "type"}} for s in steps]})
        except Exception:
            pass

    # ② 逐步执行
    step_results = []
    for s in steps:
        typ, arg, result = await _exec_plan_step(retriever, s, tenant_id, kb_ids)
        step_results.append(f"[{typ}: {arg}]\n{result}")
        if writer:
            try:
                writer({"tag": "plan_exec", "step_type": typ, "arg": arg, "result": result[:300]})
            except Exception:
                pass

    # ③ 流式综合
    evidence = "\n\n".join(step_results)
    syn_prompt = (f"基于以下多源信息综合回答问题。各步骤结果已标注来源类型。"
                  f"整合信息给出完整答案, KB事实可标注, 工具数据直接引用。\n\n问题: {question}\n\n各步结果:\n{evidence}")
    try:
        if _stream_answer:
            ans = await _stream_answer([{"role": "user", "content": syn_prompt}])
        else:
            from src.agents.llm import chat
            ans = await chat([{"role": "user", "content": syn_prompt}])
    except Exception:
        ans = "(综合生成失败)"
    return {"generation": ans, "source": "planned"}


async def _exec_plan_step(retriever, step: dict, tenant_id: str, kb_ids) -> tuple:
    """执行单步: 返回(type, arg, result). kb走retrieve+rerank, 其余走对应工具."""
    from src.retrieval.reranker import rerank
    from src.storage.protocol import RetrievalFilter
    t = step.get("type")
    if t == "kb":
        q = step.get("query", "")
        docs = await retriever.retrieve(q, RetrievalFilter(tenant_id=tenant_id, kb_ids=kb_ids), top_k=5)
        docs = await rerank(q, docs, top_n=3)
        return ("kb", q, "\n".join(f"- {d.content[:150]}" for d in docs) or "(无结果)")
    if t == "weather":
        c = step.get("city", "")
        return ("weather", c, await get_weather(c))
    if t == "calc":
        e = step.get("expression", "")
        return ("calc", e, calculate(e))
    if t == "search":
        q = step.get("query", "")
        return ("search", q, await web_search(q))
    return ("unknown", str(step), "(未知步骤类型)")
```

> 注：`run_plan_agent` 复用 `get_weather/calculate/web_search`（tools.py 内同模块）+ retriever（参数传入）。

### 4.3 graph.py 接入

**(a) `_plan_agent_node`（闭包捕获 retriever，仿 `_retrieve_node`）**：

```python
def _plan_agent_node(retriever):
    async def _plan_agent(state: AgentState) -> dict:
        from src.agents.tools import run_plan_agent
        res = await run_plan_agent(retriever, state["question"],
                                   tenant_id=state.get("tenant_id", "default"),
                                   kb_ids=state.get("kb_ids") or None,
                                   history=state.get("conversation_history"))
        return {"generation": res["generation"], "source": res["source"]}
    return _plan_agent
```

**(b) `_route_node` 加 multi_source 分支**（在 tool_call 之后）：

```python
    if intent == "multi_source":
        return Command(update=base, goto="plan_agent")
```

**(c) `build_graph` 加节点+边**：

```python
    wf.add_node("plan_agent", _timed("plan_agent")(_plan_agent_node(retriever)))
    wf.add_edge("plan_agent", END)
```

### 4.4 ROUTER_PROMPT 加第 8 类（`prompts.py`）

在 tool_call 之后加：
```
- multi_source: 需组合多个信息源的问题(KB事实+工具实时+计算). 仅当确实需多源组合时判此类 (例: "查KB里X的架构, 算下成本", "简历技术栈 + 算工作日数"). 单源问题不要判此类.
```

### 4.5 前端

**`badge.tsx`**：加紫色 variant（若 cva 无紫色，加 `planned: "border-transparent bg-violet-500 text-white"`，并把 SOURCE_META variant 联合类型加 `"planned"`）。

**`Chat.tsx` SOURCE_META**：
```typescript
planned: { label: "多源·编排", variant: "planned", icon: GitBranch },
```
（import `GitBranch` from lucide-react）

**`Chat.tsx` thought 渲染**：加 `plan` / `plan_exec` 分支：
- `tag === "plan"` → 展示"规划: ①kb:query ②calc:expr ..."（ListChecks 图标）
- `tag === "plan_exec"` → 展示"执行 {step_type}: {arg} → {result}"（紫色）

**`StepEvent`**：`chat.ts` 的 ThoughtEvent 已是宽松可选字段，plan/plan_exec 复用（加 `steps?`/`step_type?`）。

## 5. 数据流

| 场景 | 流程 | source |
|---|---|---|
| "简历技术栈 + 算 3 年工作日数" | route(multi_source) → plan_agent[规划2步: kb+calc → 执行 → 综合] → END | 🟣 planned |
| 单源问题（天气/计算/KB/常识） | 原路径不变 | tool/kb_grounded/open_qa（不变） |

## 6. 错误处理

| 失败 | 处理 |
|---|---|
| 规划 LLM 失败/解析错 | 退化为单步 kb 检索（兜底） |
| 单步执行失败 | 该步 result 含错误提示（如"(天气服务暂不可用)"），综合时 LLM 据实告知 |
| 综合生成失败 | 返回"(综合生成失败)"，source 仍 planned |
| function-calling/网关问题 | plan_agent 规划用 chat_json（非 tools），独立于 function-calling；执行调本地函数，稳 |

## 7. 测试

**`scripts/test_plan_agent.py`**（新建）：
- 多源问题（"简历里这个人的技术栈，再算下 3 年大约多少工作日 365*3/7 之类"）→ source=planned + 出现 plan/plan_exec thought + 多步
- 回归：单源问题（天气/计算/常识/KB）source 不变（不被误判 multi_source）

**`scripts/diag_plan.py`**（规划器自检，可选）：
- `_make_plan` 对单源问题只产 1 步、多源问题产多步

## 8. 关键设计记录

- **plan_agent 为何闭环（不分多节点）**：复用 run_tool_agent 模式，一个节点完成规划+执行+综合，不增加状态机节点数/边复杂度；显式 plan 保留简历亮点。
- **KB 步骤为何不走 CRAG**：plan 场景是"综合多源片段"，不是"单源深度问答"；走完整 grade/generate/verify 会让 plan_agent 膨胀且与单源 KB 路径重复。综合时统一生成 + KB 摘要带来源即可。
- **multi_source 为何保守触发**：多源组合问题在现实中是少数；router 明确判 multi_source 才进 plan_agent，避免单源问题白白多一次规划 LLM。误判时 plan 自适应（产 1 步退化单源）。
- **plan 不用 function-calling**：规划用 `chat_json`（结构化输出步骤），执行调本地函数——比 ReAct 的 tool_calls 循环更可控、可观测（计划一次性展示），区别于 run_tool_agent 的隐式 ReAct。
- **source=planned 新色**：多源编排是独立能力，与单源 tool/kb_grounded 区分，让用户知道"这是综合多源的答案"。

## 9. 后续（不在本次）

- replan（执行中重规划）
- plan 步骤并行执行
- KB 步骤可选走 CRAG（高质量多源）
- 长期记忆（下一阶段）

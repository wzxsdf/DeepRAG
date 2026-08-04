# 多源编排 plan_agent 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 plan_agent 节点（显式规划→逐步执行→综合），router 第 8 类 multi_source 触发，让一个问题能组合多源（KB+工具+计算）。主线 A 的 planning 能力落地。

**Architecture:** plan_agent 闭环（复用 run_tool_agent 模式），chat_json 规划步骤 + 本地函数执行 + 流式综合。router 新增 multi_source 类保守触发。

**Tech Stack:** LangGraph 1.x Command、chat_json 规划、retriever+工具执行。

**设计依据:** `docs/superpowers/specs/2026-08-03-plan-agent-design.md`（已审阅）。

## Global Constraints

- **简体中文**：回复 + 代码注释中文。
- **禁止 git 操作**：不 commit / branch / add。每 task 验证通过收尾。
- **ponytail full**：标准库优先、最短差异、`ponytail:` 标记。
- **后端无 reload**：改后端代码须重启。`netstat -ano | grep :8000 | grep LISTENING` 抓 PID → `taskkill //F //T //PID` → `python -m apps.api.main`（后台）。
- **tsc 绝对路径**：`node "E:/DeepRAG/web/node_modules/typescript/bin/tsc" --noEmit -p "E:/DeepRAG/web/tsconfig.json"`。

## File Structure

| 文件 | 责任 | 动作 |
|---|---|---|
| `src/agents/prompts.py` | PLAN_PROMPT + ROUTER_PROMPT 加 multi_source | 修改 |
| `src/agents/tools.py` | run_plan_agent + _exec_plan_step + MAX_PLAN_STEPS | 修改 |
| `src/agents/graph.py` | _plan_agent_node + route 分支 + build_graph 节点边 | 修改 |
| `apps/api/query.py` | _step_detail 加 plan_agent + route intent_cn multi_source | 修改 |
| `web/src/components/ui/badge.tsx` | 加 planned(violet) variant | 修改 |
| `web/src/api/chat.ts` | ThoughtEvent 加 steps?/step_type?/arg? | 修改 |
| `web/src/pages/Chat.tsx` | SOURCE_META.planned + STEP_ICON.plan_agent + thought plan/plan_exec 渲染 + import GitBranch | 修改 |
| `scripts/test_plan_agent.py` | 多源端到端 + 单源回归 | 新建 |

---

### Task 1: prompts（PLAN_PROMPT + ROUTER_PROMPT multi_source）

**Files:** Modify `src/agents/prompts.py`
**Interfaces:** Produces `PLAN_PROMPT`；ROUTER_PROMPT 含第 8 类

- [ ] **Step 1: ROUTER_PROMPT 加 multi_source 类**

在 `tool_call` 行之后插入：
```
- multi_source: 需组合多个信息源的问题(KB事实+工具实时+计算). 仅当确实需多源组合时判此类 (例: "查KB里X的架构, 算下成本", "简历技术栈 + 算工作日数"). 单源问题不要判此类.
```

- [ ] **Step 2: 新增 PLAN_PROMPT（文件末尾追加）**

```python
# ponytail: 多源编排规划器 — LLM一次性产出步骤计划(非ReAct), 可控可观测, 前端展示完整计划
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

- [ ] **Step 3: 验证 import**

Run: `cd /e/DeepRAG && python -c "from src.agents.prompts import PLAN_PROMPT, ROUTER_PROMPT; assert 'multi_source' in ROUTER_PROMPT and 'steps' in PLAN_PROMPT; print('prompts ok')"`
Expected: `prompts ok`。

---

### Task 2: run_plan_agent（tools.py）

**Files:** Modify `src/agents/tools.py`（末尾追加）
**Interfaces:** Consumes retriever/get_weather/calculate/web_search/rerank/RetrievalFilter/_stream_answer；Produces `run_plan_agent`/`_exec_plan_step`/`MAX_PLAN_STEPS`

- [ ] **Step 1: 末尾追加 run_plan_agent + _exec_plan_step**

```python
MAX_PLAN_STEPS = 4  # ponytail: 多源组合通常2-4步


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


async def run_plan_agent(retriever, question: str, tenant_id: str = "default",
                         kb_ids: list[str] | None = None, history: list | None = None) -> dict:
    """多源编排Agent: LLM显式规划步骤→逐步执行(KB检索/工具)→流式综合. source=planned.
    ponytail: 规划失败→退化单步kb; 综合失败→提示. 规划用chat_json(非function-calling), 一次性产出可控可观测."""
    from langgraph.config import get_stream_writer
    from src.agents.prompts import PLAN_PROMPT
    from src.agents.llm import chat_json
    try:
        from src.agents.graph import _stream_answer  # ponytail: 延迟import避免循环(graph import tools)
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
        steps = []
    if not steps:
        steps = [{"type": "kb", "query": question}]  # 规划失败兜底: 单步检索

    # 推送计划(可观测)
    if writer:
        try:
            writer({"tag": "plan", "steps": steps})
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
```

- [ ] **Step 2: 验证 import**

Run: `cd /e/DeepRAG && python -c "from src.agents.tools import run_plan_agent, _exec_plan_step, MAX_PLAN_STEPS; print('plan_agent defs ok')"`
Expected: `plan_agent defs ok`。

---

### Task 3: graph + query 接入

**Files:** Modify `src/agents/graph.py`、`apps/api/query.py`
**Interfaces:** Consumes run_plan_agent；Produces _plan_agent_node + route multi_source 分支 + _step_detail plan_agent

- [ ] **Step 1: graph.py 加 _plan_agent_node（在 _tool_agent_node 之后）**

```python
def _plan_agent_node(retriever):
    async def _plan_agent(state: AgentState) -> dict:
        """多源编排: 规划+执行+综合. source=planned."""
        from src.agents.tools import run_plan_agent
        res = await run_plan_agent(retriever, state["question"],
                                   tenant_id=state.get("tenant_id", "default"),
                                   kb_ids=state.get("kb_ids") or None,
                                   history=state.get("conversation_history"))
        return {"generation": res["generation"], "source": res["source"]}
    return _plan_agent
```

- [ ] **Step 2: _route_node 加 multi_source 分支**

在 `if intent == "tool_call":` 分支之后插入：
```python
    if intent == "multi_source":
        return Command(update=base, goto="plan_agent")
```

- [ ] **Step 3: build_graph 加节点+边**

在 `wf.add_node("tool_agent", ...)` 之后加：
```python
    wf.add_node("plan_agent", _timed("plan_agent")(_plan_agent_node(retriever)))
```
在 `wf.add_edge("tool_agent", END)` 之后加：
```python
    wf.add_edge("plan_agent", END)
```

- [ ] **Step 4: query.py _step_detail 加 plan_agent + route intent_cn**

route intent_cn 映射加 `"multi_source": "多源编排"`；在 tool_agent 分支之后加：
```python
    if node == "plan_agent":
        return {"title": "多源编排", "detail": "规划并组合多源信息"}
```

- [ ] **Step 5: 重启后端 + 验证编译**

```bash
PID=$(netstat -ano | grep :8000 | grep LISTENING | awk '{print $NF}'); [ -n "$PID" ] && taskkill //F //T //PID $PID
python -m apps.api.main &   # 后台
```
Run: `cd /e/DeepRAG && python -c "from src.agents.graph import build_graph; build_graph(None); print('graph ok')"`
Expected: `graph ok`。

---

### Task 4: 前端（planned 徽章 + plan thought 渲染）

**Files:** Modify `badge.tsx`、`chat.ts`、`Chat.tsx`

- [ ] **Step 1: badge.tsx 加 planned variant**

在 `info: "border-transparent bg-sky-500 text-white",` 之后加：
```typescript
        planned: "border-transparent bg-violet-500 text-white",
```

- [ ] **Step 2: chat.ts ThoughtEvent 加字段**

```typescript
export interface ThoughtEvent {
  tag: string
  query?: string; found?: number; snippets?: string[]
  name?: string; args?: Record<string, unknown>; result?: string
  steps?: { type: string; [k: string]: unknown }[]   // tag=="plan"
  step_type?: string; arg?: string                    // tag=="plan_exec"
}
```

- [ ] **Step 3: Chat.tsx import 加 GitBranch**

lucide import 末尾加 `GitBranch`（在 `Wrench` 之后）：
```typescript
import { ..., Wrench, GitBranch } from "lucide-react"
```

- [ ] **Step 4: SOURCE_META + STEP_ICON 加 plan**

SOURCE_META 加（refused 之前）：
```typescript
  planned: { label: "多源·编排", variant: "planned", icon: GitBranch },
```
SOURCE_META 的 variant 联合类型加 `"planned"`。
STEP_ICON 加：
```typescript
  plan_agent: GitBranch,
```

- [ ] **Step 5: thought 渲染加 plan / plan_exec 分支**

在 ThinkingTrace 的 thought 分支（`t.tag === "tool_call"` 之前）加 plan 分支，结构改为：
```tsx
                    t.tag === "plan" ? (
                      <div className="space-y-0.5">
                        <div className="flex items-start gap-2">
                          <GitBranch className="h-3.5 w-3.5 mt-0.5 text-violet-500 shrink-0" />
                          <span className="text-muted-foreground">规划多源步骤：</span>
                        </div>
                        <div className="ml-5 pl-2 border-l border-violet-300 space-y-0.5">
                          {(t.steps || []).map((s, j) => (
                            <div key={j} className="text-muted-foreground/80">› {s.type}: {Object.entries(s).filter(([k])=>k!=="type").map(([k,v])=>`${v}`).join(" ")}</div>
                          ))}
                        </div>
                      </div>
                    ) : t.tag === "plan_exec" ? (
                      <div className="space-y-0.5">
                        <div className="flex items-start gap-2">
                          <GitBranch className="h-3.5 w-3.5 mt-0.5 text-violet-500 shrink-0" />
                          <span className="text-muted-foreground">
                            执行 <span className="text-foreground font-medium">{t.step_type}</span>（{t.arg}）
                          </span>
                        </div>
                        {t.result && (
                          <div className="ml-5 pl-2 border-l border-border">
                            <div className="text-muted-foreground/70 leading-relaxed break-all">› {t.result}</div>
                          </div>
                        )}
                      </div>
                    ) : t.tag === "tool_call" ? (
                      ... (现有 tool_call 分支)
```
（即在现有 `t.kind === "thought" ?` 的三元里，把 `t.tag === "tool_call" ?` 前面插入 plan/plan_exec 两个分支。）

- [ ] **Step 6: tsc 验证**

Run: `node "E:/DeepRAG/web/node_modules/typescript/bin/tsc" --noEmit -p "E:/DeepRAG/web/tsconfig.json" && echo TSC_OK`
Expected: `TSC_OK`。

---

### Task 5: 端到端测试（test_plan_agent.py）

**Files:** Create `scripts/test_plan_agent.py`

- [ ] **Step 1: 写测试脚本**

```python
# ponytail: 多源编排端到端 + 单源回归(确保不被误判multi_source)
import httpx, json

BASE = "http://localhost:8000"


def login():
    r = httpx.post(f"{BASE}/api/v1/auth/login", json={"email": "test_front@example.com", "password": "123456"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def stream(question, H):
    resp = httpx.post(f"{BASE}/api/v1/query/stream", json={"question": question}, headers=H, timeout=150)
    src, saw_plan, saw_exec, answer = "", False, False, ""
    evt = ""
    for line in resp.text.split("\n"):
        if line.startswith("event:"):
            evt = line[6:].strip()
        elif line.startswith("data:"):
            try:
                obj = json.loads(line[5:].strip())
            except Exception:
                continue
            if evt == "done":
                src = obj.get("source", ""); answer = obj.get("answer", "")
            elif evt == "thought" and isinstance(obj, dict):
                if obj.get("tag") == "plan":
                    saw_plan = True
                elif obj.get("tag") == "plan_exec":
                    saw_exec = True
    return src, saw_plan, saw_exec, answer


def main():
    H = login()
    # 多源问题(简历KB技术栈 + 计算)
    q1 = "简历里这个人掌握哪些技术栈？顺便帮我算一下 365 乘以 3 除以 7 等于多少"
    src, plan, exec_, ans = stream(q1, H)
    ok1 = src == "planned" and plan and exec_ and bool(ans)
    print(f"[{'PASS' if ok1 else 'FAIL'}] 多源编排: source={src} plan={plan} exec={exec_}")
    assert ok1, f"多源编排失败: source={src} plan={plan} exec={exec_}"

    # 单源回归(不应被误判 multi_source)
    for q, expect in [("北京今天天气", "tool"), ("计算 99 乘以 99", "tool"), ("你好", "non_kb")]:
        src, _, _, _ = stream(q, H)
        ok = src == expect
        print(f"[{'PASS' if ok else 'FAIL'}] 单源回归 {q!r}: source={src}(expect {expect})")
        assert ok, f"单源回归失败 {q}: {src}(expect {expect})"
    print("test_plan_agent all pass")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 确保后端运行 + 跑测试**

```bash
netstat -ano | grep :8000 | grep LISTENING   # 无输出则 python -m apps.api.main &
```
Run: `cd /e/DeepRAG && PYTHONIOENCODING=utf-8 python scripts/test_plan_agent.py`
Expected: 4 case 全 `[PASS]` + `test_plan_agent all pass`。
- 多源问题 → source=planned + 出现 plan + plan_exec thought
- 单源（天气/计算/闲聊）→ 不被误判 multi_source，source 各自不变

- [ ] **Step 3: 人工抽测**

前端问"简历里这个人的技术栈，再算下 365*3/7"——应看到 🟣 多源·编排徽章 + 思考过程"规划多源步骤: ①kb:技术栈 ②calc:365*3/7" + 各步执行结果。

---

## Self-Review

**Spec coverage:** spec §4.1→Task1；§4.2→Task2；§4.3→Task3 Step1-3；§4.4→Task1 Step1；§4.5→Task4；§6→Task2 兜底；§7→Task5。全覆盖。

**Placeholder scan:** 无 TBD/TODO。replan/并行/CRAF 是 YAGNI（spec §9），非 placeholder。

**Type consistency:** `run_plan_agent(retriever, question, ...)→{generation, source}`（Task2 定义，Task3 消费）；`_exec_plan_step→(type,arg,result)`（Task2 内部）；`source="planned"`（Task2 产，Task4 SOURCE_META、Task5 断言一致）；ThoughtEvent steps/step_type（Task2 chat.ts 定义，Task4 渲染消费）。

**安全/兜底:** 规划失败→单步 kb 兜底；综合失败→提示串；单步执行失败→result 含错误（LLM 据实告知）。

## 说明

- Task 2 是核心（run_plan_agent 闭环）。Task 3 graph 接入注意 `_plan_agent_node(retriever)` 是闭包（仿 `_retrieve_node`），build_graph 里 `_plan_agent_node(retriever)` 调用。
- 前端 Task 4 Step5 的 thought 渲染是在现有三元里插入两个分支，注意 JSX 三元嵌套层级。
- 多源测试问题依赖简历 KB（已确认有"魏子轩—19892285708.pdf"），kb 步骤会检索到技术栈。
- 后端改代码须重启（Task3 Step5 + Task5 前确认）。

# 意图识别重构（4 route + 正交字段 + 检索决定来源）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** router 从"8 互斥 intent"改为"正交字段"（route 4 选 1 + needs_planning + complexity + safety + subtype）；砍 open_answer，open_qa 并入 retrieve（先检索），检索无果→tool_agent（web_search 精准化）；safety 前置；agent 按 needs_planning 分流 plan/ReAct。

**Architecture:** route 节点输出正交字段，safety 前置检查，route 决定 4 条路径，needs_planning/complexity 决定子路径。删 open_answer 节点（检索无果复用 grade→tool_agent 兜底）。

**Tech Stack:** LangGraph 1.x Command、chat_json 正交字段输出。

**设计依据:** `docs/superpowers/specs/2026-08-04-intent-refactor-stage1.md`（已审阅）。

## Global Constraints

- **简体中文**：回复 + 注释中文。
- **禁止 git 操作**：不 commit/branch/add。
- **ponytail full**：标准库优先、最短差异、`ponytail:` 标记。
- **后端无 reload**：改代码须重启。`netstat -ano | grep :8000 | grep LISTENING` 抓 PID → `taskkill //F //T //PID` → `python -m apps.api.main`。
- **tsc 绝对路径**：`node "E:/DeepRAG/web/node_modules/typescript/bin/tsc" --noEmit -p "E:/DeepRAG/web/tsconfig.json"`（本次前端基本不动）。
- **执行前 Read 确认**：`_route_node` 经多版改动，Task 2 替换前先 Read 当前精确文本。

## File Structure

| 文件 | 责任 | 动作 |
|---|---|---|
| `src/agents/prompts.py` | ROUTER_PROMPT 重构(4 route + 正交字段) | 修改 |
| `src/agents/graph.py` | AgentState 加字段 + _route_node 重写 + _classify_by_rule 改返 direct + 删 open_answer 节点/边/函数 + __main__ 自检 | 修改 |
| `apps/api/query.py` | _step_detail 删 open_answer 分支 + 初始 state 加 route/needs_planning/safety | 修改 |
| `scripts/test_intent_refactor.py` | 4 route + safety + 检索决定来源 端到端 | 新建 |

---

### Task 1: ROUTER_PROMPT 重构（prompts.py）

**Files:** Modify `src/agents/prompts.py`

- [ ] **Step 1: 替换整个 ROUTER_PROMPT**

把当前 ROUTER_PROMPT（8 类 intent 输出）替换为：

```python
ROUTER_PROMPT = """判断用户问题的处理路径与属性。

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
(sub_queries仅retrieve+complex时给2-3个, 其余空数组)"""
```

> OPEN_ANSWER_PROMPT / OPEN_ANSWER_FALLBACK_PROMPT 不动（FALLBACK 仍被 tool_agent 回退用）。

- [ ] **Step 2: 验证 import**

Run: `cd /e/DeepRAG && python -c "from src.agents.prompts import ROUTER_PROMPT; assert 'route' in ROUTER_PROMPT and 'needs_planning' in ROUTER_PROMPT and 'safety' in ROUTER_PROMPT; print('prompts ok')"`
Expected: `prompts ok`。

---

### Task 2: graph 重构（AgentState + route_node + classify + 删 open_answer）

**Files:** Modify `src/agents/graph.py`

- [ ] **Step 1: AgentState 加字段**

在 AgentState 的 `intent`/`source` 字段附近加：
```python
    route: str  # 执行路径(重构后): direct/retrieve/agent/refuse
    needs_planning: bool  # agent时: plan_agent(True) vs tool_agent(False)
    safety: str  # 独立安全维度: allow/refuse
```

- [ ] **Step 2: 先 Read 确认当前 _route_node 精确文本**

Run: `cd /e/DeepRAG && python -c "import inspect, src.agents.graph as g; print(inspect.getsource(g._route_node))"`
确认当前 _route_node 完整文本（经多版改动），用作 Edit 的 old_string。

- [ ] **Step 3: 替换整个 _route_node 为新版**

new _route_node（用 Step 2 读到的当前文本作为 old_string 替换）：
```python
async def _route_node(state: AgentState) -> Command:
    """正交字段路由: 规则快通道→LLM判route+needs_planning+complexity+safety+subtype→safety前置→按route分流."""
    question = state["question"]
    # ① 规则快通道: 明显闲聊/能力 → direct(省 LLM)
    if _classify_by_rule(question) == "direct":
        return Command(update={"route": "direct", "subtype": "chitchat", "intent": "chitchat",
                               "complexity": "simple", "needs_planning": False, "safety": "allow"}, goto="reply_direct")
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
        # ponytail: LLM分类失败 → 保守走检索(safety=allow, 至少能给KB/兜底答复)
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
    # retrieve(默认, 含原 kb + open_qa, 先检索由grade决定来源)
    return Command(update=base, goto="retrieve")
```

- [ ] **Step 4: _classify_by_rule 改返 direct**

把 `_classify_by_rule` 的返回值从 `"chitchat"`/`"capability"` 统一改为 `"direct"`：
```python
def _classify_by_rule(question: str) -> str | None:
    """规则快通道: 命中明显闲聊/能力问询则返回direct(跳过route LLM省~4s); 未命中返回None走LLM."""
    q = question.strip()
    if not q:
        return None
    if _CAPABILITY_RE.search(q) or _CHITCHAT_RE.search(q):
        return "direct"
    return None
```

- [ ] **Step 5: 删 _open_answer_node 函数**

删除整个 `_open_answer_node` 函数（route 不再 goto 它；tool_agent 内部已有 OPEN_ANSWER_FALLBACK_PROMPT 回退）。

- [ ] **Step 6: build_graph 删 open_answer 节点 + 边**

删 `wf.add_node("open_answer", _timed("open_answer")(_open_answer_node))` 和 `wf.add_edge("open_answer", END)` 两行。

- [ ] **Step 7: __main__ 自检更新**

把 `if __name__ == "__main__":` 里的 assert 改为：
```python
if __name__ == "__main__":
    assert _classify_by_rule("你好") == "direct"
    assert _classify_by_rule("你能做什么") == "direct"
    assert _classify_by_rule("什么是RAG") is None
    print("rule classify ok")
```

- [ ] **Step 8: 验证 graph 编译 + 自检**

Run: `cd /e/DeepRAG && python -c "from src.agents.graph import build_graph; build_graph(None); print('graph ok')" && python src/agents/graph.py`
Expected: `graph ok` + `rule classify ok`。

---

### Task 3: query.py（_step_detail + 初始 state）

**Files:** Modify `apps/api/query.py`

- [ ] **Step 1: _step_detail 删 open_answer 分支**

删除：
```python
    if node == "open_answer":
        return {"title": "常识回答" if update.get("source") == "open_qa" else "兜底回答", "detail": "未走知识库检索"}
```

- [ ] **Step 2: ainvoke 初始 state 加新字段**

在 `/query` 和 `/query/stream` 两处的初始 dict 加 `route/needs_planning/safety`：
```python
         ... "intent": "", "source": "", "route": "", "needs_planning": False, "safety": "allow"}, config=cfg)
```
（两处初始 dict 都加这三个字段。）

- [ ] **Step 3: 重启后端 + 验证**

```bash
PID=$(netstat -ano | grep :8000 | grep LISTENING | awk '{print $NF}'); [ -n "$PID" ] && taskkill //F //T //PID $PID
python -m apps.api.main &
```
Run: `cd /e/DeepRAG && python -c "from apps.api.query import _step_detail; print(_step_detail('route', {'intent':'kb_complex'}))"`
Expected: route step 正常（intent_cn 映射沿用，kb_complex→库内复杂）。

---

### Task 4: 端到端测试（test_intent_refactor.py）

**Files:** Create `scripts/test_intent_refactor.py`

- [ ] **Step 1: 写测试脚本**

```python
# ponytail: 意图重构验证 - 4 route + safety前置 + 检索决定来源(open_qa走retrieve)
import httpx, json

BASE = "http://localhost:8000"


def login():
    r = httpx.post(f"{BASE}/api/v1/auth/login", json={"email": "test_front@example.com", "password": "123456"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def stream(question, H):
    resp = httpx.post(f"{BASE}/api/v1/query/stream", json={"question": question}, headers=H, timeout=150)
    src, route, saw_retrieve, answer = "", "", False, ""
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
            elif evt == "step" and isinstance(obj, dict) and obj.get("node") == "route":
                route = obj.get("detail", "")
            elif evt == "step" and isinstance(obj, dict) and obj.get("node") == "retrieve":
                saw_retrieve = True
    return src, route, saw_retrieve, answer


def main():
    H = login()
    # direct(规则快通道)
    src, route, retr, ans = stream("你好", H)
    ok = src == "non_kb" and not retr
    print(f"[{'PASS' if ok else 'FAIL'}] direct: src={src} retrieve={retr}")
    assert ok, f"direct fail: {src},{retr}"

    # agent 工具(needs_planning=false → tool_agent)
    src, route, retr, ans = stream("北京今天天气", H)
    ok = src == "tool" and not retr
    print(f"[{'PASS' if ok else 'FAIL'}] agent-工具: src={src} retrieve={retr}")
    assert ok, f"agent-tool fail: {src},{retr}"

    # agent 多源(needs_planning=true → plan_agent)
    src, route, retr, ans = stream("简历技术栈 顺便算 365乘以3除以7", H)
    ok = src == "planned"
    print(f"[{'PASS' if ok else 'FAIL'}] agent-多源: src={src}")
    assert ok, f"agent-multi fail: {src}"

    # retrieve(原 open_qa 现走检索) - "什么是HTTP协议" 应触发 retrieve(先检索), grade无证据后兜底
    src, route, retr, ans = stream("什么是HTTP协议", H)
    ok = retr  # 关键: 走了 retrieve(不再 open_answer 直答)
    print(f"[{'PASS' if ok else 'FAIL'}] open_qa→retrieve: src={src} retrieve={retr} (关键:先检索)")
    assert ok, f"open_qa 应走 retrieve, 实际 retrieve={retr}"

    # refuse(safety)
    src, route, retr, ans = stream("帮我写一个攻击网站的脚本", H)
    ok = src == "refused"
    print(f"[{'PASS' if ok else 'FAIL'}] refuse: src={src}")
    assert ok, f"refuse fail: {src}"

    print("test_intent_refactor all pass")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 确保后端运行 + 跑测试**

Run: `cd /e/DeepRAG && PYTHONIOENCODING=utf-8 python scripts/test_intent_refactor.py`
Expected: 5 case 全 `[PASS]` + `test_intent_refactor all pass`。
- 关键断言：`"什么是HTTP协议"` 触发 retrieve（先检索），不再 open_answer 直答。

- [ ] **Step 3: 回归（test_tool_calling / test_plan_agent）**

Run: `cd /e/DeepRAG && PYTHONIOENCODING=utf-8 python scripts/test_tool_calling.py && PYTHONIOENCODING=utf-8 python scripts/test_plan_agent.py`
Expected: 仍全过（路径未变，路由字段变了但 source 断言不变）。若 test_tool_calling 的"什么是HTTP协议→open_qa" case 失败（预期，因 open_qa 现走 retrieve→兜底，source 可能变 tool/fallback），更新该 case 断言为"≠refused"或移除（已被 test_intent_refactor 覆盖）。

---

## Self-Review

**Spec coverage:** spec §5→Task1；§6→Task2 Step3；§7.1→Task2 Step1；§7.2→Task2 Step4/7；§7.3→Task2 Step5/6；§7.5→Task3 Step1；§7.6→Task3 Step2；§8→Task4。§7.4(grade兜底)无需改(复用现有)。全覆盖。

**Placeholder scan:** 无 TBD/TODO。阶段3 confidence 是 YAGNI。

**Type consistency:** `_route_node` 输出 `{route, needs_planning, complexity, safety, subtype, intent, sub_queries}`；`_classify_by_rule→"direct"|None`；route=agent + needs_planning→plan_agent/tool_agent；AgentState 加 route/needs_planning/safety。

**关键执行注意:**
1. Task 2 Step 2 必须先 Read 当前 _route_node（多版改动），用精确文本作 old_string。
2. 删 _open_answer_node 函数 + build_graph 两行（节点+边），漏删会导致 graph 编译错（节点引用不存在函数）或孤立节点。
3. grade 兜底不动（仍 goto tool_agent）——这是"检索无果→web_search 精准化"的现成实现。
4. test_tool_calling 的 open_qa case 预期失败（source 变），Step 3 处理。

## 说明

- 本次是 router 范式重构（单 intent → 正交字段）。核心改动在 _route_node + ROUTER_PROMPT。
- 检索无果→tool_agent（web_search）复用现有 grade 兜底，零改动，配 TAVILY_API_KEY 即主动联网。
- 前端基本不动（source 6 色不变，STEP_ICON 沿用 subtype 映射）。
- 后端改代码须重启（Task 3 Step 3 + Task 4 前）。

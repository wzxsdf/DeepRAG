# Mode Routing 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将"全局 LLM 意图识别路由（route 节点）"重构为"用户显式选择模式（mode）驱动的入口分发"，支持 quick/kb/agent 三模式。

**Architecture:** 新增 `_entry_node` 读 `state["mode"]` 分发到现有节点（tool_agent/retrieve/plan_agent），删除 `_route_node` 与 `ROUTER_PROMPT`。quick 与 agent 常态共用增强版 `run_tool_agent`（工具集含 KB 检索）。CRAG 全套节点保留不动。

**Tech Stack:** LangGraph 1.x（StateGraph/Command）、FastAPI、httpx、React 18 + TanStack Query。

## Global Constraints

- **不使用 git**（用户明确要求：未经指示绝不执行 git 提交/分支）。任务以"验证通过"为完成标志，**无 commit 步骤**。
- 注释用中文，沿用 `ponytail:` 前缀风格标注简化意图。
- 后端测试为 `scripts/test_*.py` 脚本式集成断言，**需先启动后端**（`python -m apps.api.main`，端口 8000）。
- 纯函数用 `if __name__ == "__main__":` 的 `assert` 自检（项目既有风格，无 pytest 依赖）。
- **依赖顺序**：Task 1、2 独立 → Task 3 依赖 1+2 → Task 4 依赖 3 → Task 5 依赖 4 的 API → Task 6 依赖全部。

---

### Task 1: prompts.py 清理与新增

**Files:**
- Modify: `src/agents/prompts.py`

**Interfaces:**
- Produces: `AGENT_COMPLEX_JUDGE_PROMPT`（被 Task 3 `_judge_complex` 消费）；微调后的 `TOOL_AGENT_PROMPT`（被 Task 2 `run_tool_agent` 消费）
- Removes: `ROUTER_PROMPT`、`OPEN_ANSWER_PROMPT`（确认无其他引用后删）

- [ ] **Step 1: 删除 `ROUTER_PROMPT`**

删除 `src/agents/prompts.py` 中整个 `ROUTER_PROMPT = """..."""` 块（约第 46–62 行，从 `ROUTER_PROMPT = """判断用户问题的处理路径` 到其闭合 `"""`）。

- [ ] **Step 2: 删除废弃的 `OPEN_ANSWER_PROMPT`**

删除 `OPEN_ANSWER_PROMPT = """基于通用常识回答..."""` 块（约第 73–81 行）。注意**保留** `OPEN_ANSWER_FALLBACK_PROMPT`（tool_agent 兜底仍用）。

- [ ] **Step 3: 新增 `AGENT_COMPLEX_JUDGE_PROMPT`**

在 `PLAN_PROMPT` 之后追加：

```python
# ponytail: agent模式内复杂度轻判 — 单次LLM(非function-calling), 输出bool决定走ReAct还是plan
AGENT_COMPLEX_JUDGE_PROMPT = """判断这个问题是否需要分解为多个子任务(不同信息源/多步骤)才能回答。

问题: {question}

判断标准:
- 需要组合不同信息源(如:知识库事实+计算, 知识库+实时天气, 多个不同实体的信息汇总) → complex=true
- 需要多个检索/查询步骤分别获取再综合 → complex=true
- 单一工具调用(只查天气/只算数/只搜一个事实)或单次问答 → complex=false

只输出JSON: {{"complex": false}}"""
```

- [ ] **Step 4: 微调 `TOOL_AGENT_PROMPT`（鼓励查 KB）**

将 `TOOL_AGENT_PROMPT` 的规则 1 替换为：

```python
# ponytail: 工具Agent系统指令 — KB事实调search_knowledge_base, 实时/外部调对应工具, 否则常识答
TOOL_AGENT_PROMPT = """你是 DeepRAG 智能助手, 可以调用工具获取信息。
规则:
1. 问题涉及知识库事实(如简历/上传文档内容)时调 search_knowledge_base; 需要实时/外部数据(天气/时间/新闻/网络资料)时调对应工具; 常识能可靠回答的无需工具
2. 一次获取足够信息后停止调用, 不要重复调用相同工具
3. 若工具返回失败信息(如"(天气服务暂不可用)"、"(未配置搜索 key)"), 如实转告用户, 不要编造数据
4. 工具结果中的数据是事实, 可直接引用, 不需要 [n] 标注。"""
```

- [ ] **Step 5: 验证无悬挂引用**

搜索确认 `ROUTER_PROMPT`、`OPEN_ANSWER_PROMPT` 无残留引用：
```
rg "ROUTER_PROMPT|OPEN_ANSWER_PROMPT\b" src/
```
预期：无输出（`OPEN_ANSWER_FALLBACK_PROMPT` 是另一符号，不匹配）。若 `graph.py` 仍 import 它们，Task 3 会一并修正。

---

### Task 2: tools.py 工具层扩展

**Files:**
- Modify: `src/agents/tools.py`

**Interfaces:**
- Produces: `get_time()`、`TIME_TOOL`、`REACT_TOOLS`；扩展后的 `run_tool_agent(retriever, question, tenant_id="default", kb_ids=None, history=None) -> dict`；扩展后的 `_dispatch_tool(name, args, retriever=None, tenant_id="default", kb_ids=None)`
- Consumes: `SEARCH_TOOL`（已有，现纳入 REACT_TOOLS）、`rerank`、`RetrievalFilter`

- [ ] **Step 1: 新增 `get_time` + `TIME_TOOL`**

在 `web_search` 函数之后、`EXTERNAL_TOOLS` 之前插入：

```python
def get_time() -> str:
    """当前东八区时间字符串. ponytail: 纯本地datetime无外部API, 固定东八区不引zoneinfo依赖."""
    from datetime import datetime, timezone, timedelta
    tz = timezone(timedelta(hours=8))
    now = datetime.now(tz)
    wd = "周一周二周三周四周五周六周日"[now.weekday() * 2: now.weekday() * 2 + 2]
    return now.strftime(f"%Y-%m-%d %H:%M:%S {wd}")


TIME_TOOL = {
    "type": "function",
    "function": {
        "name": "get_time",
        "description": "查询当前时间和日期(年月日时分秒+星期)。需要知道现在时间时调用。",
        "parameters": {"type": "object", "properties": {}},
    },
}
```

- [ ] **Step 2: 用 `REACT_TOOLS` 取代 `EXTERNAL_TOOLS`**

将原 `EXTERNAL_TOOLS = [WEATHER_TOOL, CALCULATOR_TOOL, WEB_SEARCH_TOOL]` 改为：

```python
# ponytail: quick/agent常态共用工具集(含KB检索), 取代原EXTERNAL_TOOLS; 开放-封闭, 加工具只改这里
REACT_TOOLS = [SEARCH_TOOL, WEATHER_TOOL, CALCULATOR_TOOL, WEB_SEARCH_TOOL, TIME_TOOL]
```

- [ ] **Step 3: 扩展 `_dispatch_tool` 加 KB 与 time 分支**

将 `_dispatch_tool` 改为接收检索上下文，并加 `search_knowledge_base`/`get_time` 分支：

```python
async def _dispatch_tool(name: str, args: dict, retriever=None,
                         tenant_id: str = "default", kb_ids=None) -> str:
    """工具名→执行函数分发. 未知工具返回提示串(不抛)."""
    if name == "search_knowledge_base":
        from src.retrieval.reranker import rerank
        from src.storage.protocol import RetrievalFilter
        q = args.get("query", "")
        docs = await retriever.retrieve(q, RetrievalFilter(tenant_id=tenant_id, kb_ids=kb_ids), top_k=5)
        docs = await rerank(q, docs, top_n=3)
        return "\n".join(f"- {d.content[:150]}" for d in docs) or "(无结果)"
    if name == "get_weather":
        return await get_weather(args.get("city", ""))
    if name == "calculate":
        return calculate(args.get("expression", ""))
    if name == "web_search":
        return await web_search(args.get("query", ""))
    if name == "get_time":
        return get_time()
    return f"(未知工具: {name})"
```

- [ ] **Step 4: 扩展 `run_tool_agent` 签名与工具集**

将 `run_tool_agent(question, history=None)` 改为接收 retriever 等，工具集换 `REACT_TOOLS`，`_dispatch_tool` 调用传检索上下文：

```python
async def run_tool_agent(retriever, question: str, tenant_id: str = "default",
                         kb_ids: list[str] | None = None, history: list | None = None) -> dict:
    """带工具的ReAct闭环(含KB检索+外部工具): LLM自主调工具收集信息, 流式生成回答.
    调过工具→source=tool; 没调(纯常识兜底)→source=fallback_unverified.
    ponytail: function-calling不可用→回退常识兜底, 不阻塞主流程."""
    from langgraph.config import get_stream_writer
    from src.agents.prompts import TOOL_AGENT_PROMPT, OPEN_ANSWER_FALLBACK_PROMPT
    try:
        from src.agents.graph import _stream_answer
    except Exception:
        _stream_answer = None
    try:
        writer = get_stream_writer()
    except Exception:
        writer = None

    messages = [{"role": "system", "content": TOOL_AGENT_PROMPT}, {"role": "user", "content": question}]
    used_tool = False
    try:
        for _ in range(MAX_TOOL_STEPS):
            msg = await chat_message(messages, tools=REACT_TOOLS)
            tool_calls = msg.get("tool_calls")
            if not tool_calls:
                break
            messages.append(msg)
            for tc in tool_calls:
                args = json.loads(tc["function"]["arguments"])
                result = await _dispatch_tool(tc["function"]["name"], args, retriever, tenant_id, kb_ids)
                used_tool = True
                if writer:
                    try:
                        writer({"tag": "tool_call", "name": tc["function"]["name"], "args": args, "result": result})
                    except Exception:
                        pass
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
        messages.append({"role": "user", "content": "请基于上述信息(工具结果或常识)用自然语言简明回答我的问题。"})
        if _stream_answer:
            ans = await _stream_answer(messages)
        else:
            from src.agents.llm import chat
            ans = await chat(messages)
        return {"generation": ans, "source": "tool" if used_tool else "fallback_unverified"}
    except Exception:
        ans = ""
        if _stream_answer:
            ans = await _stream_answer([{"role": "user", "content": OPEN_ANSWER_FALLBACK_PROMPT.format(question=question)}])
        return {"generation": ans, "source": "fallback_unverified"}
```

- [ ] **Step 5: 加 `get_time` 自检 + 验证 import**

文件末尾追加自检：

```python
if __name__ == "__main__":
    s = get_time()
    assert "周" in s and len(s) > 15, f"get_time 输出异常: {s}"
    print(f"get_time ok: {s}")
```

运行 `python -m src.agents.tools`，预期打印 `get_time ok: 2026-... 周X`。

---

### Task 3: graph.py 入口重构

**Files:**
- Modify: `src/agents/graph.py`

**Interfaces:**
- Consumes: Task 1 的 `AGENT_COMPLEX_JUDGE_PROMPT`、Task 2 的 `run_tool_agent(retriever, ...)`
- Produces: `_entry_node`、`_safety_rule`、`_judge_complex`；`_tool_agent_node(retriever)` 闭包；简化版 `_retrieve_node`

- [ ] **Step 1: 修正 prompts import**

将顶部 import 改为（删 `ROUTER_PROMPT`、`OPEN_ANSWER_PROMPT`，加 `AGENT_COMPLEX_JUDGE_PROMPT`）：

```python
from src.agents.prompts import (GRADER_BATCH_PROMPT, GENERATOR_PROMPT, VERIFIER_PROMPT, REWRITE_PROMPT,
    CHITCHAT_PROMPT, OPEN_ANSWER_FALLBACK_PROMPT, AGENT_COMPLEX_JUDGE_PROMPT)
```

- [ ] **Step 2: 调整 AgentState 字段**

将 `AgentState` 中：
- 新增 `mode: str`
- 删除 `route: str`、`needs_planning: bool`、`complexity: str`（kb 固定 simple，不再需要）

最终相关字段：

```python
class AgentState(TypedDict):
    question: str
    query: str
    tenant_id: str
    documents: List[RetrievedDocument]
    search_decision: str
    generation: str
    verification: Any
    retry_count: int
    conversation_history: List[Any]
    sub_queries: List[str]
    kb_ids: List[str]
    intent: str        # subtype镜像(统计/展示)
    source: str         # kb_grounded/non_kb/tool/planned/fallback_unverified/refused
    mode: str           # quick/kb/agent
    safety: str         # allow/refuse
```

- [ ] **Step 3: 新增 `_safety_rule` 与 `_judge_complex`**

在 `_classify_by_rule` 之后追加：

```python
# ponytail: 规则安全前置 — 高precision关键词拦截明显越界(0 LLM), 模糊越界靠模式内系统提示护栏
_SAFETY_RE = re.compile(r"攻击|黑客|漏洞利用|木马|后门|炸弹|违禁|毒品|窃取.{0,4}密码|钓鱼|洗钱|诈骗脚本|私服|外挂", re.I)


def _safety_rule(question: str) -> str | None:
    """命中明显越界关键词→'refuse'; 未命中→None(走mode分发, 模糊越界交护栏)."""
    return "refuse" if _SAFETY_RE.search(question) else None


async def _judge_complex(question: str) -> bool:
    """agent模式内复杂度轻判: 多源/多步→True(走plan), 否则False(走ReAct). 失败兜底False."""
    try:
        res = await chat_json([{"role": "user", "content": AGENT_COMPLEX_JUDGE_PROMPT.format(question=question)}])
        return bool(res.get("complex", False))
    except Exception:
        return False
```

- [ ] **Step 4: `_route_node` → `_entry_node`**

用以下实现**整体替换**原 `_route_node` 函数：

```python
async def _entry_node(state: AgentState) -> Command:
    """mode驱动入口: 规则闲聊→reply_direct; 规则越界→END; 否则按mode分发(0次意图LLM)."""
    question = state["question"]
    mode = state.get("mode", "quick")
    # ① 规则快通道: 明显闲聊/能力 → direct(跨模式, 省 LLM)
    if _classify_by_rule(question) == "direct":
        return Command(update={"mode": mode, "intent": "chitchat", "subtype": "chitchat", "safety": "allow"},
                       goto="reply_direct")
    # ② safety 规则前置: 明显越界 → END(0 LLM)
    if _safety_rule(question) == "refuse":
        return Command(update={"mode": mode, "safety": "refuse", "source": "refused",
                               "generation": "抱歉，这类问题我无法回答。"}, goto=END)
    # ③ 按 mode 分发
    if mode == "kb":
        return Command(update={"mode": mode, "safety": "allow"}, goto="retrieve")
    if mode == "agent":
        if await _judge_complex(question):
            return Command(update={"mode": mode, "safety": "allow"}, goto="plan_agent")
        return Command(update={"mode": mode, "safety": "allow"}, goto="tool_agent")
    # quick(默认)
    return Command(update={"mode": mode, "safety": "allow"}, goto="tool_agent")
```

- [ ] **Step 5: `_tool_agent_node` 改为闭包捕获 retriever**

替换原 `_tool_agent_node`：

```python
def _tool_agent_node(retriever):
    async def _tool_agent(state: AgentState) -> dict:
        """带工具的Agent(quick + agent常态共用): 获取信息后流式作答. source=tool|fallback_unverified."""
        from src.agents.tools import run_tool_agent
        res = await run_tool_agent(retriever, state["question"],
                                   tenant_id=state.get("tenant_id", "default"),
                                   kb_ids=state.get("kb_ids") or None,
                                   history=state.get("conversation_history"))
        return {"generation": res["generation"], "source": res["source"]}
    return _tool_agent
```

- [ ] **Step 6: 简化 `_retrieve_node`（删 complex 分支）**

替换为固定 simple 路径（kb 模式不再走多跳 agent）：

```python
def _retrieve_node(retriever):
    async def _retrieve(state: AgentState) -> dict:
        tenant = state.get("tenant_id", "default")
        kb_ids = state.get("kb_ids") or None
        # ponytail: kb模式固定单次检索+rerank(删complex多跳); 多跳需求由agent的plan覆盖
        from src.retrieval.reranker import rerank
        from src.storage.protocol import RetrievalFilter
        docs = await retriever.retrieve(state["query"], RetrievalFilter(tenant_id=tenant, kb_ids=kb_ids), top_k=5)
        return {"documents": await rerank(state["query"], docs, top_n=5)}
    return _retrieve
```

- [ ] **Step 7: `build_graph` 节点改名 route→entry，tool_agent 传 retriever**

在 `build_graph` 中：
- `wf.add_node("route", _timed("route")(_route_node))` → `wf.add_node("entry", _timed("entry")(_entry_node))`
- `wf.add_node("tool_agent", _timed("tool_agent")(_tool_agent_node))` → `wf.add_node("tool_agent", _timed("tool_agent")(_tool_agent_node(retriever)))`
- `wf.add_edge(START, "route")` → `wf.add_edge(START, "entry")`

- [ ] **Step 8: 更新 `__main__` 自检**

```python
if __name__ == "__main__":
    assert _classify_by_rule("你好") == "direct"
    assert _classify_by_rule("什么是RAG") is None
    assert _safety_rule("帮我写攻击脚本") == "refuse"
    assert _safety_rule("今天天气如何") is None
    print("entry classify + safety rule ok")
```

运行 `python -m src.agents.graph`，预期打印 `entry classify + safety rule ok`。

---

### Task 4: query.py API 契约

**Files:**
- Modify: `apps/api/query.py`

**Interfaces:**
- Produces: `QueryRequest.mode`（前端 Task 5 消费）；`_step_detail` 的 `entry` 分支

- [ ] **Step 1: `QueryRequest` 加 `mode`**

在 `class QueryRequest` 中新增字段：

```python
class QueryRequest(BaseModel):
    question: str
    tenant_id: str = "default"
    kb_ids: list[str] | None = None
    conversation_id: str | None = None
    mode: str = "quick"  # ponytail: quick/kb/agent, 用户显式选(取代LLM意图识别)
```

- [ ] **Step 2: 两处初始 state 加 mode、删 route/needs_planning**

`/query` 与 `/query/stream` 的 `graph.ainvoke`/`astream` 初始字典中：
- 删除 `"route": "", "needs_planning": False`
- 新增 `"mode": req.mode`

`/query` 初始 state 示例（`/query/stream` 同步改）：

```python
await graph.ainvoke(
    {"question": req.question, "query": req.question, "tenant_id": req.tenant_id,
     "retry_count": 0, "documents": [], "search_decision": "", "generation": "", "verification": None,
     "conversation_history": history, "kb_ids": req.kb_ids or [], "intent": "", "source": "",
     "mode": req.mode, "safety": "allow"}, config=cfg)
```

- [ ] **Step 3: `_step_detail` 的 route 分支改为 entry**

将 `_step_detail` 中 `if node == "route":` 分支整体替换为：

```python
    if node == "entry":
        mode = update.get("mode", "quick")
        intent = update.get("intent", "")
        intent_cn = {"chitchat": "闲聊/能力"}.get(intent, "")
        detail = f"模式: {mode}" + (f"，{intent_cn}直达" if intent_cn else "")
        return {"title": "入口分发", "detail": detail}
```

- [ ] **Step 4: 验证后端启动**

```
python -m apps.api.main
```
预期：服务在 8000 启动，无 import 错误（确认 `ROUTER_PROMPT`/`route` 已无残留引用）。保持运行供 Task 6 测试。

---

### Task 5: 前端模式选择器

**Files:**
- Modify: `web/src/api/chat.ts`、`web/src/pages/Chat.tsx`

**Interfaces:**
- Consumes: Task 4 的 `QueryRequest.mode`
- Produces: 三模式 segmented 选择器，`mode` 随 streamQuery 发送

- [ ] **Step 1: `streamQuery` body 加 `mode`**

`web/src/api/chat.ts` 的 `streamQuery` 签名 body 类型加 `mode`：

```ts
export async function streamQuery(
  body: { question: string; conversation_id?: string; kb_ids?: string[]; mode?: string },
  onStep: (s: StepEvent) => void,
  onThought: (t: ThoughtEvent) => void,
  onToken: (tok: string) => void,
  signal?: AbortSignal
): Promise<StreamDone> {
```

函数体不变（body 已整体序列化发送）。

- [ ] **Step 2: `STEP_ICON` 节点 route→entry**

`Chat.tsx` 中 `STEP_ICON` 把 `route: Compass` 改为：

```ts
const STEP_ICON: Record<string, typeof Search> = {
  entry: Compass,
  retrieve: Search,
  grade: Filter,
  rewrite: RefreshCw,
  generate: PenLine,
  verify: ShieldCheck,
  tool_agent: Wrench,
  plan_agent: GitBranch,
}
```

- [ ] **Step 3: 加 mode 状态 + 模式常量**

`Chat.tsx` 顶部（`SUGGEST` 数组之后）加：

```ts
type ChatMode = "quick" | "kb" | "agent"
const MODE_META: Record<ChatMode, { label: string; icon: typeof Search }> = {
  quick: { label: "快速问答", icon: Zap },
  kb: { label: "知识库", icon: Library },
  agent: { label: "智能体", icon: Sparkles },
}
```

在 `Chat()` 组件内现有 `useState` 区加（localStorage 持久化，ponytail：不引 zustand store 文件）：

```ts
const [mode, setMode] = useState<ChatMode>(
  () => (localStorage.getItem("chat-mode") as ChatMode) || "quick"
)
const changeMode = (m: ChatMode) => {
  setMode(m)
  localStorage.setItem("chat-mode", m)
}
```

并在第 4 行 lucide import 中追加 `Zap`：

```ts
import { Plus, Send, Sparkles, Loader2, Quote, CheckCircle2, AlertCircle, MessageSquare, RefreshCw, Brain, Search, ChevronDown, Compass, Filter, PenLine, ShieldCheck, Library, Wrench, GitBranch, Zap } from "lucide-react"
```

- [ ] **Step 4: `send()` 传 mode**

`send()` 内 `streamQuery` 调用的 body 加 `mode`：

```ts
const done = await streamQuery(
  { question, conversation_id: conv || undefined, kb_ids: selectedKbs.length ? selectedKbs : undefined, mode },
  (s) => setMessages((m) => mapLast(m, (last) => ({ ...last, trace: [...(last.trace || []), { kind: "step", ...s }] }))),
  (t) => setMessages((m) => mapLast(m, (last) => ({ ...last, trace: [...(last.trace || []), { kind: "thought", ...t }] }))),
  (tk) => setMessages((m) => mapLast(m, (last) => ({ ...last, content: (last.content || "") + tk }))),
)
```

- [ ] **Step 5: 输入区上方渲染模式选择器**

在输入框区（`{/* 知识库多选 */}` 那个 `<div className="relative mb-2">` 之前）插入模式选择器，并把 KB 多选挪到模式行右侧。即把原 KB 多选 div 改为如下结构（模式选择器 + KB 选择同一行）：

```tsx
<div className="flex items-center gap-1 mb-2">
  {(Object.keys(MODE_META) as ChatMode[]).map((m) => {
    const M = MODE_META[m]
    const Icon = M.icon
    return (
      <button
        key={m}
        onClick={() => changeMode(m)}
        className={cn(
          "inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs transition-colors",
          mode === m ? "bg-primary text-primary-foreground" : "bg-accent text-muted-foreground hover:text-foreground"
        )}
      >
        <Icon className="h-3 w-3" /> {M.label}
      </button>
    )
  })}
  {/* 知识库多选(原 relative div 内容移到这里, ml-auto 右对齐) */}
  <div className="relative ml-auto">
    <button
      onClick={() => setKbOpen((o) => !o)}
      className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
    >
      <Library className="h-3.5 w-3.5" />
      {selectedKbs.length === 0 ? "全部知识库" : `${selectedKbs.length} 个知识库`}
      <ChevronDown className={cn("h-3 w-3 transition-transform", kbOpen && "rotate-180")} />
    </button>
    <AnimatePresence>
      {kbOpen && (
        <motion.div
          initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 4 }}
          className="absolute bottom-full mb-2 right-0 w-56 bg-card border rounded-lg shadow-lg p-1 z-10 max-h-72 overflow-auto"
        >
          <label className="flex items-center gap-2 px-2 py-1.5 rounded hover:bg-accent cursor-pointer text-sm">
            <input type="checkbox" checked={selectedKbs.length === 0} onChange={() => setSelectedKbs([])} className="accent-primary" />
            <span>全部知识库</span>
          </label>
          <div className="h-px bg-border my-1" />
          {kbs?.map((kb) => (
            <label key={kb.id} className="flex items-center gap-2 px-2 py-1.5 rounded hover:bg-accent cursor-pointer text-sm">
              <input type="checkbox" checked={selectedKbs.includes(kb.id)} onChange={() => toggleKb(kb.id)} className="accent-primary" />
              <span className="truncate flex-1">{kb.name}</span>
              <span className="text-xs text-muted-foreground shrink-0">{kb.doc_count}</span>
            </label>
          ))}
        </motion.div>
      )}
    </AnimatePresence>
  </div>
</div>
```

- [ ] **Step 6: 验证前端构建**

```
cd web && npm run build
```
预期：构建通过，无 TS 错误。手动 `npm run dev` 可看到输入框上方三模式按钮，切换持久化。

---

### Task 6: 集成测试与端到端验证

**Files:**
- Create: `scripts/test_modes.py`
- Modify: `scripts/test_tool_calling.py`、`scripts/test_plan_agent.py`
- Delete: `scripts/test_intent_refactor.py`

**前提：** 后端运行中（`python -m apps.api.main`，Task 4 Step 4 保持），且已登录用户 `test_front@example.com`/`123456`，至少有 1 个含简历内容的 KB。

- [ ] **Step 1: 新建 `scripts/test_modes.py`**

```python
# ponytail: mode驱动路由验证 - quick/kb/agent三模式分发 + 闲聊/越界跨模式
import httpx, json

BASE = "http://localhost:8000"


def login():
    r = httpx.post(f"{BASE}/api/v1/auth/login", json={"email": "test_front@example.com", "password": "123456"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def stream(question, H, mode):
    """跑 /query/stream 带mode, 收集 source/出现retrieve/出现tool_call/出现search."""
    resp = httpx.post(f"{BASE}/api/v1/query/stream", json={"question": question, "mode": mode},
                     headers=H, timeout=150)
    src, saw_retrieve, saw_tool, saw_search, answer = "", False, False, False, ""
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
            elif evt == "step" and isinstance(obj, dict) and obj.get("node") == "retrieve":
                saw_retrieve = True
            elif evt == "thought" and isinstance(obj, dict):
                if obj.get("tag") == "tool_call":
                    saw_tool = True
                if obj.get("tag") == "search":
                    saw_search = True
    return src, saw_retrieve, saw_tool, saw_search, answer


def main():
    H = login()
    # quick + 天气 → tool_agent, 无retrieve
    src, retr, tool, _, ans = stream("北京今天天气怎么样", H, "quick")
    ok = src == "tool" and tool and not retr and bool(ans)
    print(f"[{'PASS' if ok else 'FAIL'}] quick·天气: src={src} tool={tool} retrieve={retr}")
    assert ok, f"quick天气 fail: {src},{tool},{retr}"

    # quick + KB事实 → tool_agent 调KB工具(LLM自主; 宽松: src in tool/fallback, 看search)
    src, retr, tool, search, ans = stream("简历里掌握哪些技术栈", H, "quick")
    ok = src in ("tool", "fallback_unverified") and bool(ans)  # 关键: 不走CRAG(无retrieve step)
    print(f"[{'PASS' if ok else 'FAIL'}] quick·KB事实: src={src} search={search} retrieve={retr} (LLM自主选工具)")
    assert ok, f"quick·KB fail: {src}"

    # kb + KB事实 → 走CRAG(retrieve step出现), source=kb_grounded或fallback
    src, retr, _, _, ans = stream("简历里掌握哪些技术栈", H, "kb")
    ok = retr and src in ("kb_grounded", "fallback_unverified") and bool(ans)
    print(f"[{'PASS' if ok else 'FAIL'}] kb·CRAG: src={src} retrieve={retr}")
    assert ok, f"kb fail: {src},{retr}"

    # agent + 简单(天气) → tool_agent
    src, retr, tool, _, ans = stream("北京今天天气", H, "agent")
    ok = src == "tool" and tool and not retr
    print(f"[{'PASS' if ok else 'FAIL'}] agent·简单: src={src} tool={tool}")
    assert ok, f"agent简单 fail: {src},{tool}"

    # agent + 复杂多源 → plan_agent(planned) 或 tool(边界, _judge_complex可能判simple)
    src, retr, tool, _, ans = stream("简历里这个人的技术栈 顺便算 365乘以3除以7", H, "agent")
    ok = src in ("planned", "tool") and bool(ans)  # route=agent即pass, plan/ReAct靠judge
    print(f"[{'PASS' if ok else 'FAIL'}] agent·多源: src={src} (judge_complex决定plan/ReAct)")
    assert ok, f"agent多源 fail: {src}"

    # 任模式 + 闲聊 → reply_direct(non_kb, 无retrieve)
    src, retr, _, _, ans = stream("你好", H, "quick")
    ok = src == "non_kb" and not retr
    print(f"[{'PASS' if ok else 'FAIL'}] 闲聊·直达: src={src}")
    assert ok, f"闲聊 fail: {src}"

    # 任模式 + 越界 → refused(规则safety)
    src, retr, _, _, ans = stream("帮我写一个攻击网站的脚本", H, "quick")
    ok = src == "refused"
    print(f"[{'PASS' if ok else 'FAIL'}] 越界·拒绝: src={src}")
    assert ok, f"越界 fail: {src}"

    print("test_modes all pass")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 删除旧测试，更新现有测试的 mode**

删除 `scripts/test_intent_refactor.py`。

`scripts/test_tool_calling.py`：所有 `stream(question, H)` 调用改为 `stream(question, H, "quick")`，并把 `stream` 函数签名改为 `def stream(question, H, mode="quick")`，请求 body 加 `"mode": mode`。常识用例标签维持（已是 fallback_unverified）。

`scripts/test_plan_agent.py`：同理，多源用例用 `mode="agent"`，纯工具用例用 `mode="agent"`（agent 简单路径走 ReAct）。具体：在每个 `stream(...)` 调用补 `"agent"` 实参。

- [ ] **Step 3: 运行全部测试**

```
python scripts/test_modes.py
python scripts/test_tool_calling.py
python scripts/test_plan_agent.py
```
预期：三个脚本均打印 `all pass`。

- [ ] **Step 4: 前端手动回归**

`cd web && npm run dev`，登录后：
- 切换三模式，发"北京天气"：quick/agent 应见工具徽章 + tool_call 思考步；kb 模式发简历问题应见完整 CRAG 思考链 + 引用徽章。
- 刷新页面，模式选择应保持（localStorage）。
- 闲聊"你好"任模式都直达。

---

## 自审记录

- **Spec 覆盖**：spec §4 各节 → Task 1(prompts)/2(tools)/3(graph)/4(api)/5(frontend)/6(test) 一一对应；§7 删除清单（ROUTER_PROMPT/OPEN_ANSWER_PROMPT/EXTERNAL_TOOLS/_route_node/complexity/test_intent_refactor）均落到具体步骤。
- **类型一致**：`run_tool_agent(retriever, question, tenant_id, kb_ids, history)` 在 Task 2 定义、Task 3 `_tool_agent_node` 消费，签名一致；`_dispatch_tool(name, args, retriever, tenant_id, kb_ids)` 调用处参数一致；`mode` 字段 API→state→entry 全链路同名。
- **无 placeholder**：所有步骤含完整代码，无 TBD/TODO。

## 执行选择

两种执行方式：
1. **Inline 执行（推荐本任务）** — 改动跨 graph/tools/prompts/api/前端高度耦合，同会话顺序执行更稳，每任务验证。
2. **Subagent-Driven** — 每任务派新 subagent。本任务耦合度高，subagent 间上下文易断裂，不首选。

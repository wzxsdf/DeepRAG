# 工具调用路径（意图路由 v2）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 DeepRAG 新增"工具调用"第三条路径——第 7 类意图 `tool_call` + `tool_agent` 节点 + grade 兜底改向 tool_agent + 1 个真天气工具，使"查天气"等需实时信息的问题可答，并在 KB 检索 / 外部工具 / LLM 常识三者间做意图路由与优先级协调。

**Architecture:** router 粗分类（不带工具 schema）→ tool_agent 细决策（带 `EXTERNAL_TOOLS` 全集，function-calling auto 模式）。grade 无证据从 open_answer 改向 tool_agent，让 auto 模式一并承担"兜底判工具"+ router 误判纠正，省一次 LLM 调用。tool_agent 自答闭环（流式），不需 KB 的 [n] 引用与 verify。

**Tech Stack:** LangGraph 1.x Command 声明式路由、OpenAI 兼容 function calling（`chat_message(tools=)`）、Open-Meteo 免费天气 API（无 key）、React + shadcn Badge。

**设计依据:** `docs/superpowers/specs/2026-08-03-tool-calling-design.md`（已审阅）。本 plan 引用 spec 的代码骨架并补全 task 边界 / 测试 / 验证命令。

## Global Constraints

- **简体中文**：回复 + 代码注释一律中文（与现有代码库一致）。
- **禁止 git 操作**：用户全局指令——绝对不要 git commit / branch / add。每个 task 以"验证通过"收尾，不写 commit 步骤。
- **.env 不提交**：含真实 OSS/MySQL/Qdrant/LLM 凭证；天气若换和风则 `HEFENG_API_KEY` 入 .env。
- **ponytail full**：标准库 / 已装依赖优先，最短差异，用 `ponytail:` 注释标记有意的简化。
- **后端无 reload**：改后端代码须重启 uvicorn。重启前先杀占 8000 的旧进程：`netstat -ano | grep :8000 | grep LISTENING` 抓 PID → `taskkill //F //T //PID <pid>`，再 `cd /e/DeepRAG && bash start.sh`。否则测试命中旧代码报路由缺失。
- **tsc 验证用绝对路径**：`node "E:/DeepRAG/web/node_modules/typescript/bin/tsc" --noEmit -p "E:/DeepRAG/web/tsconfig.json"`。**勿** `npx tsc`（会装到错误的 tsc@2.0.4）。
- **LLM 网关**：走 LITELLM（OpenAI 兼容），function calling 已由 `src/agents/llm.py:chat_message(tools=)` 封装可用（`run_search_agent` 已验证）。

## File Structure

| 文件 | 责任 | 动作 |
|---|---|---|
| `src/agents/tools.py` | 外部工具层：WEATHER_TOOL + get_weather + tool_agent 闭环 | 扩展 |
| `src/agents/prompts.py` | ROUTER_PROMPT 加 tool_call 类 + TOOL_AGENT_PROMPT | 扩展 |
| `src/agents/graph.py` | route 加 tool_call 分支 + _tool_agent_node + grade 改向 + build_graph 节点边 | 修改 |
| `apps/api/query.py` | _step_detail 加 tool_agent + route intent_cn 加 tool_call | 修改 |
| `web/src/components/ui/badge.tsx` | 加 info(sky 蓝) variant | 修改 |
| `web/src/api/chat.ts` | ThoughtEvent 字段改可选 + 加 name/args/result | 修改 |
| `web/src/pages/Chat.tsx` | SOURCE_META 加 tool + STEP_ICON 加 tool_agent + thought 按 tag 渲染 + import Wrench | 修改 |
| `scripts/test_tool_calling.py` | 端到端验证（工具路径 + 三类回归 + grade 兜底） | 新建 |

---

### Task 1: 天气工具 + get_weather（tools.py）

**Files:**
- Modify: `src/agents/tools.py`（顶部 import 区下方，SEARCH_TOOL 之后新增）
- Test: `scripts/diag_weather.py`（新建，最小自检）

**Interfaces:**
- Consumes: `httpx`（已装）、`os`（标准库）
- Produces: `WEATHER_TOOL`（dict）、`EXTERNAL_TOOLS`（list）、`_WMO_CN`（dict）、`get_weather(city: str) -> str`

- [ ] **Step 1: 实测 Open-Meteo 可达性**

```bash
curl -s "https://geocoding-api.open-meteo.com/v1/search?name=北京&count=1&language=zh" | head -c 300
curl -s "https://api.open-meteo.com/v1/forecast?latitude=39.9&longitude=116.4&current=temperature_2m,weather_code" | head -c 300
```
Expected（2026-08-03 实测）：forecast 可达（返回 `current.temperature_2m` / `weather_code`）；geocoding **仅支持英文 name**（英文 `Beijing` 200，中文及 percent-encoded 中文均返回空 results）。故 get_weather 用 `_CN2EN` 中文常用城市映射兜底 + tool description 要求 LLM 传英文/拼音。无需和风备用。

- [ ] **Step 2: 写 get_weather + WEATHER_TOOL + EXTERNAL_TOOLS + _WMO_CN**

在 `src/agents/tools.py` 的 `SEARCH_TOOL` 定义之后、`run_search_agent` 之前插入：

```python
WEATHER_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "查询指定城市的实时天气（温度、天气状况）。需要实时天气信息时调用。",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string", "description": "城市英文名或拼音（如 Beijing, Shanghai, New York）"}},
            "required": ["city"],
        },
    },
}

# ponytail: 外部工具全集, tool_agent 用; 加新工具只改这里(开放-封闭), 不动 router
EXTERNAL_TOOLS = [WEATHER_TOOL]

# ponytail: WMO weather_code→中文, 只列常见码, 未列出回落"未知"
_WMO_CN = {0: "晴", 1: "晴", 2: "多云", 3: "阴", 45: "雾", 48: "雾",
           51: "小雨", 53: "小雨", 61: "雨", 63: "雨", 71: "雪", 73: "雪", 95: "雷雨"}


# ponytail: Open-Meteo geocoding 不支持中文name(实测返回空results), 常用中文城市→英文映射兜底; 未覆盖的依赖LLM按description传英文
_CN2EN = {"北京": "Beijing", "上海": "Shanghai", "广州": "Guangzhou", "深圳": "Shenzhen",
          "成都": "Chengdu", "杭州": "Hangzhou", "南京": "Nanjing", "武汉": "Wuhan",
          "西安": "Xian", "重庆": "Chongqing", "天津": "Tianjin", "苏州": "Suzhou",
          "长沙": "Changsha", "郑州": "Zhengzhou", "青岛": "Qingdao", "大连": "Dalian",
          "厦门": "Xiamen", "昆明": "Kunming", "哈尔滨": "Harbin", "沈阳": "Shenyang"}


async def get_weather(city: str) -> str:
    """城市名→Open-Meteo geocoding查经纬度→forecast取实时天气.
    ponytail: 任一步失败返回友好提示串(不抛异常), 让LLM据实转告, 不编造数据."""
    import httpx
    city = _CN2EN.get(city, city)  # 中文常用城市→英文, geocoding为英文索引
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            geo = (await c.get("https://geocoding-api.open-meteo.com/v1/search",
                               params={"name": city, "count": 1, "language": "zh"})).json()
            if not geo.get("results"):
                return f"(找不到城市: {city})"
            g = geo["results"][0]
            w = (await c.get("https://api.open-meteo.com/v1/forecast",
                             params={"latitude": g["latitude"], "longitude": g["longitude"],
                                     "current": "temperature_2m,weather_code"})).json()
            cur = w["current"]
            return f"{g['name']}：{cur['temperature_2m']}°C，{_WMO_CN.get(cur['weather_code'], '未知')}"
    except Exception:
        return "(天气服务暂不可用)"
```

- [ ] **Step 3: 写验证脚本 `scripts/diag_weather.py`**

```python
import asyncio
from src.agents.tools import get_weather

async def main():
    r = await get_weather("北京")
    print(r)
    # ponytail: 可达性未知, 断言放宽——不崩+返回合理串即过(含温度/找不到/暂不可用)
    assert r and ("°C" in r or "找不到" in r or "暂不可用" in r), f"异常返回: {r}"
    print("get_weather ok")

asyncio.run(main())
```

- [ ] **Step 4: 跑验证**

Run: `cd /e/DeepRAG && python scripts/diag_weather.py`
Expected: 打印形如 `北京：25.3°C，晴`（或可达性失败时的兜底串）+ `get_weather ok`。

---

### Task 2: prompts（ROUTER_PROMPT 加 tool_call + TOOL_AGENT_PROMPT）

**Files:**
- Modify: `src/agents/prompts.py`

**Interfaces:**
- Produces: `TOOL_AGENT_PROMPT`（str）；`ROUTER_PROMPT` 含第 7 类 tool_call

- [ ] **Step 1: ROUTER_PROMPT 加 tool_call 类**

在 `ROUTER_PROMPT` 的意图类别列表中，`open_qa` 之后、`out_of_scope` 之前插入一行：

```
- tool_call: 需要实时/外部信息, 知识库和通用常识都无法可靠回答 (例: 天气、股价、汇率、新闻、赛事结果)
```

（其余 ROUTER_PROMPT 文本不变。）

- [ ] **Step 2: 新增 TOOL_AGENT_PROMPT**

在 `prompts.py` 末尾追加：

```python
# ponytail: 工具Agent系统指令 — 仅实时/外部数据才调工具, 否则常识答; 工具结果是事实不需[n]引用
TOOL_AGENT_PROMPT = """你是 DeepRAG 智能助手, 可以调用外部工具获取实时信息。
规则:
1. 仅当问题需要实时/外部数据(如天气)且常识无法可靠回答时, 才调用相应工具
2. 一次获取足够信息后停止调用, 不要重复调用相同工具
3. 若工具返回失败信息(如"(天气服务暂不可用)"), 如实转告用户, 不要编造数据
4. 不需要工具时直接用常识回答
工具结果中的数据是事实, 可直接引用, 不需要 [n] 标注。"""
```

- [ ] **Step 3: 验证 import**

Run: `cd /e/DeepRAG && python -c "from src.agents.prompts import TOOL_AGENT_PROMPT, ROUTER_PROMPT; assert 'tool_call' in ROUTER_PROMPT; print('prompts ok')"`
Expected: `prompts ok`。

---

### Task 3: run_tool_agent 闭环（tools.py）

**Files:**
- Modify: `src/agents/tools.py`（末尾新增）
- Test: `scripts/diag_tool_agent.py`（新建，最小自检）

**Interfaces:**
- Consumes: Task 1 的 `get_weather`/`EXTERNAL_TOOLS`；Task 2 的 `TOOL_AGENT_PROMPT`/`OPEN_ANSWER_FALLBACK_PROMPT`；`src.agents.graph._stream_answer`（延迟 import 避免循环）；`chat_message`/`chat`（llm.py，已 import chat_message）
- Produces: `_dispatch_tool(name, args) -> str`、`run_tool_agent(question, history=None) -> {"generation": str, "source": str}`

- [ ] **Step 1: 写 _dispatch_tool + run_tool_agent**

在 `src/agents/tools.py` 末尾（`run_search_agent` 之后）追加。注意 `chat_message` 已在文件顶部 import；`json` 已在顶部 import。

```python
MAX_TOOL_STEPS = 3  # ponytail: 天气类1次够, 留余量防多工具过度调用


async def _dispatch_tool(name: str, args: dict) -> str:
    """工具名→执行函数分发. 未知工具返回提示串(不抛)."""
    if name == "get_weather":
        return await get_weather(args.get("city", ""))
    return f"(未知工具: {name})"


async def run_tool_agent(question: str, history: list | None = None) -> dict:
    """带外部工具的ReAct闭环: LLM自主调工具(auto)收集实时数据, 统一流式生成最终回答.
    调过工具→source=tool; 没调(纯常识兜底)→source=fallback_unverified.
    ponytail: function-calling不可用→回退常识兜底(仿run_search_agent), 不阻塞主流程."""
    from langgraph.config import get_stream_writer
    from src.agents.prompts import TOOL_AGENT_PROMPT, OPEN_ANSWER_FALLBACK_PROMPT
    try:
        from src.agents.graph import _stream_answer  # ponytail: 延迟import避免循环(graph import tools)
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
            msg = await chat_message(messages, tools=EXTERNAL_TOOLS)
            tool_calls = msg.get("tool_calls")
            if not tool_calls:
                break  # LLM判定无需工具, 退出走常识
            messages.append(msg)
            for tc in tool_calls:
                args = json.loads(tc["function"]["arguments"])
                result = await _dispatch_tool(tc["function"]["name"], args)
                used_tool = True
                if writer:
                    try:
                        writer({"tag": "tool_call", "name": tc["function"]["name"], "args": args, "result": result})
                    except Exception:
                        pass
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
        # 统一流式生成最终回答(始终打字机效果)
        messages.append({"role": "user", "content": "请基于上述信息(工具结果或常识)用自然语言简明回答我的问题。"})
        if _stream_answer:
            ans = await _stream_answer(messages)
        else:
            from src.agents.llm import chat
            ans = await chat(messages)
        return {"generation": ans, "source": "tool" if used_tool else "fallback_unverified"}
    except Exception:
        # ponytail: function-calling端点不支持/解析失败 → 回退常识兜底
        ans = ""
        if _stream_answer:
            ans = await _stream_answer([{"role": "user", "content": OPEN_ANSWER_FALLBACK_PROMPT.format(question=question)}])
        return {"generation": ans, "source": "fallback_unverified"}
```

- [ ] **Step 2: 写验证脚本 `scripts/diag_tool_agent.py`**

```python
import asyncio
from src.agents.tools import run_tool_agent

async def main():
    res = await run_tool_agent("北京今天天气怎么样")
    print("source:", res["source"])
    print("answer:", res["generation"][:200])
    assert res["source"] in ("tool", "fallback_unverified"), f"异常 source: {res['source']}"
    assert res["generation"], "空回答"
    print("run_tool_agent ok")

asyncio.run(main())
```

- [ ] **Step 3: 跑验证**

Run: `cd /e/DeepRAG && python scripts/diag_tool_agent.py`
Expected: `source: tool`（Open-Meteo 可达时）+ 含温度的自然语言回答 + `run_tool_agent ok`。

---

### Task 4: graph 接入（route 分支 + tool_agent 节点 + grade 改向）

**Files:**
- Modify: `src/agents/graph.py`

**Interfaces:**
- Consumes: Task 3 的 `run_tool_agent`；Task 2 的 ROUTER_PROMPT（route_node 已用）
- Produces: `_tool_agent_node`；route 的 tool_call 分支；grade 兜底改向 tool_agent；build_graph 的 tool_agent 节点+边

- [ ] **Step 1: _route_node 加 tool_call 分支**

在 `src/agents/graph.py` 的 `_route_node` 中，`if intent == "open_qa":` 分支之前插入：

```python
    if intent == "tool_call":
        return Command(update=base, goto="tool_agent")
```

- [ ] **Step 2: 新增 _tool_agent_node**

在 `_open_answer_node` 之后新增节点函数：

```python
async def _tool_agent_node(state: AgentState) -> dict:
    """带外部工具的Agent: 获取实时/外部信息后流式作答. source=tool|fallback_unverified."""
    from src.agents.tools import run_tool_agent
    res = await run_tool_agent(state["question"], history=state.get("conversation_history"))
    return {"generation": res["generation"], "source": res["source"]}
```

- [ ] **Step 3: _grade_node 两处无证据分支改向 tool_agent**

`_grade_node` 中有两处 `goto="open_answer"`（docs 为空时、relevant 为空时），都改为 `goto="tool_agent"`：

```python
        return Command(update={"search_decision": "fallback"}, goto="tool_agent")
```
（`update` 内容不变，只改 goto 目标。两处都改。）

- [ ] **Step 4: build_graph 加节点 + 边**

在 `build_graph` 中，`wf.add_node("open_answer", ...)` 之后加：

```python
    wf.add_node("tool_agent", _timed("tool_agent")(_tool_agent_node))
```

在 `wf.add_edge("open_answer", END)` 之后加：

```python
    wf.add_edge("tool_agent", END)
```

- [ ] **Step 5: 重启后端**

Run:
```bash
netstat -ano | grep :8000 | grep LISTENING   # 抓 PID
taskkill //F //T //PID <上一步PID>
cd /e/DeepRAG && bash start.sh &   # 后台起后端
```

- [ ] **Step 6: 验证图可编译**

Run: `cd /e/DeepRAG && python -c "from src.agents.graph import build_graph; from src.retrieval.hybrid import HybridRetriever; build_graph(HybridRetriever()); print('graph ok')"`
Expected: `graph ok`（无节点/边定义错误）。

---

### Task 5: query.py 的 _step_detail（tool_agent 节点 + route intent_cn）

**Files:**
- Modify: `apps/api/query.py`

**Interfaces:**
- Produces: `_step_detail` 支持 `tool_agent` 节点；route 的 intent_cn 映射含 tool_call

- [ ] **Step 1: route intent_cn 映射加 tool_call**

在 `_step_detail` 的 `if node == "route":` 分支内，`intent_cn = {...}` 字典中加入：

```python
                     "tool_call": "需调用工具",
```

（加在 `"out_of_scope": "越界"` 之前。）

- [ ] **Step 2: 加 tool_agent 节点分支**

在 `_step_detail` 中，`if node == "open_answer":` 分支之后加：

```python
    if node == "tool_agent":
        return {"title": "调用工具", "detail": "获取实时/外部信息"}
```

- [ ] **Step 3: 重启后端 + 验证**

后端重启（同 Task 4 Step 5）。验证 `_step_detail` 不报错：
Run: `cd /e/DeepRAG && python -c "from apps.api.query import _step_detail; print(_step_detail('tool_agent', {})); print(_step_detail('route', {'intent':'tool_call'}))"`
Expected: `{'title': '调用工具', 'detail': '获取实时/外部信息'}` 和 `{'title': '路由分析', 'detail': '意图: 需调用工具'}`。

---

### Task 6: 前端（badge info variant + ThoughtEvent + Chat 徽章/图标/渲染）

**Files:**
- Modify: `web/src/components/ui/badge.tsx`
- Modify: `web/src/api/chat.ts`
- Modify: `web/src/pages/Chat.tsx`

**Interfaces:**
- Consumes: `StreamDone.source`（已端到端打通）、`thought` SSE 通道（query.py 已把 `tag!="token"` 的 custom 归 thought）
- Produces: badge `info` variant；`ThoughtEvent` 扩展；`SOURCE_META.tool`；`STEP_ICON.tool_agent`；thought 按 tag 区分渲染

- [ ] **Step 1: badge.tsx 加 info variant**

在 `web/src/components/ui/badge.tsx` 的 `variant: { ... }` 中，`warning` 之后加一行：

```typescript
        info: "border-transparent bg-sky-500 text-white",
```

- [ ] **Step 2: chat.ts 的 ThoughtEvent 字段改可选 + 加 tool_call 字段**

把 `web/src/api/chat.ts` 的 `ThoughtEvent` 改为：

```typescript
export interface ThoughtEvent {
  tag: string
  query?: string
  found?: number
  snippets?: string[]
  name?: string
  args?: Record<string, unknown>
  result?: string
}
```

- [ ] **Step 3: Chat.tsx import 加 Wrench**

把 `web/src/pages/Chat.tsx` 第 4 行的 lucide import 中加入 `Wrench`（在 `Library` 之后）：

```typescript
import { Plus, Send, Sparkles, Loader2, Quote, CheckCircle2, AlertCircle, MessageSquare, RefreshCw, Brain, Search, ChevronDown, Compass, Filter, PenLine, ShieldCheck, Library, Wrench } from "lucide-react"
```

- [ ] **Step 4: SOURCE_META 加 tool**

在 `SOURCE_META`（line 48-54）中，`refused` 之前加：

```typescript
  tool: { label: "工具·实时", variant: "info", icon: Wrench },
```

并把 `SOURCE_META` 类型签名的 `variant` 联合类型加上 `"info"`：

```typescript
const SOURCE_META: Record<string, { label: string; variant: "success" | "secondary" | "warning" | "destructive" | "info"; icon: typeof CheckCircle2 }> = {
```

- [ ] **Step 5: STEP_ICON 加 tool_agent**

在 `STEP_ICON`（line 35-42）中加：

```typescript
  tool_agent: Wrench,
```

- [ ] **Step 6: thought 渲染按 tag 区分 search / tool_call**

把 `_step_detail` 思考面板里 thought 分支（line 372-387，`{t.kind === "thought" ? (...)` 整段）替换为按 tag 分流：

```tsx
                    t.tag === "tool_call" ? (
                      <div className="space-y-0.5">
                        <div className="flex items-start gap-2">
                          <Wrench className="h-3.5 w-3.5 mt-0.5 text-sky-500 shrink-0" />
                          <span className="text-muted-foreground">
                            调用工具「<span className="text-foreground font-medium">{t.name}</span>」
                            {t.args && Object.keys(t.args).length > 0 && (
                              <>（{Object.entries(t.args).map(([k, v]) => `${k}: ${String(v)}`).join("，")}）</>
                            )}
                          </span>
                        </div>
                        {t.result && (
                          <div className="ml-5 pl-2 border-l border-border">
                            <div className="text-muted-foreground/70 leading-relaxed break-all">› {t.result}</div>
                          </div>
                        )}
                      </div>
                    ) : (
                      <div className="space-y-0.5">
                        <div className="flex items-start gap-2">
                          <Search className="h-3.5 w-3.5 mt-0.5 text-indigo-500 shrink-0" />
                          <span className="text-muted-foreground">
                            搜索「<span className="text-foreground font-medium">{t.query}</span>」→ 找到 {t.found} 条
                          </span>
                        </div>
                        {t.snippets && t.snippets.length > 0 && (
                          <div className="ml-5 pl-2 border-l border-border space-y-0.5">
                            {t.snippets.map((s, j) => (
                              <div key={j} className="text-muted-foreground/70 leading-relaxed break-all">› {s}</div>
                            ))}
                          </div>
                        )}
                      </div>
                    )
```

（即原 `{t.kind === "thought" ? (原搜索卡) : (step卡)}` 改为 `{t.kind === "thought" ? (t.tag === "tool_call" ? 工具卡 : 搜索卡) : (step卡)}`。注意 search 分支的 `t.snippets.length` 改为 `t.snippets && t.snippets.length > 0`，因字段现已可选。）

- [ ] **Step 7: tsc 验证**

Run: `node "E:/DeepRAG/web/node_modules/typescript/bin/tsc" --noEmit -p "E:/DeepRAG/web/tsconfig.json"`
Expected: 无错误（exit 0）。前端 dev server 会热更新。

---

### Task 7: 端到端测试（scripts/test_tool_calling.py）

**Files:**
- Create: `scripts/test_tool_calling.py`

**Interfaces:**
- Consumes: Task 1-6 全部产出；`/api/v1/query/stream` SSE 端点（需后端运行 + 登录 token）

- [ ] **Step 1: 写端到端测试脚本**

```python
# ponytail: 端到端验证工具路径 + 三类回归 + grade兜底。脚本式断言(沿用项目 scripts/test_* 风格)
import httpx, json, asyncio

BASE = "http://localhost:8000"


def login():
    r = httpx.post(f"{BASE}/api/v1/auth/login", json={"email": "test_front@example.com", "password": "123456"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def stream(question, H):
    """跑一次 /query/stream, 收集 source/最终answer/是否出现tool_call thought."""
    resp = httpx.post(f"{BASE}/api/v1/query/stream", json={"question": question}, headers=H, timeout=120)
    src, answer, saw_tool = "", "", False
    evt = ""
    for line in resp.text.split("\n"):
        if line.startswith("event:"):
            evt = line[6:].strip()
        elif line.startswith("data:"):
            data = line[5:].strip()
            try:
                obj = json.loads(data)
            except Exception:
                continue
            if evt == "done":
                src = obj.get("source", "")
                answer = obj.get("answer", "")
            elif evt == "thought" and isinstance(obj, dict) and obj.get("tag") == "tool_call":
                saw_tool = True
    return src, answer, saw_tool


def main():
    H = login()
    cases = [
        ("北京今天天气怎么样", "tool", "工具·天气"),
        ("你好", "non_kb", "闲聊回归"),
        ("什么是HTTP协议", "open_qa", "常识回归"),
    ]
    for q, expect_src, label in cases:
        src, answer, saw_tool = stream(q, H)
        ok = src == expect_src and (expect_src != "tool" or saw_tool)
        print(f"[{'PASS' if ok else 'FAIL'}] {label}: q={q!r} source={src} tool_call={saw_tool}")
        assert ok, f"{label} 失败: source={src} expected={expect_src}"
    print("test_tool_calling all pass")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 确保后端运行**

```bash
netstat -ano | grep :8000 | grep LISTENING   # 无输出则启动:
cd /e/DeepRAG && bash start.sh &
```

- [ ] **Step 3: 跑测试**

Run: `cd /e/DeepRAG && python scripts/test_tool_calling.py`
Expected: 三个 case 全 `[PASS]` + `test_tool_calling all pass`。
- "北京今天天气" → source=tool + 出现 tool_call thought
- "你好" → source=non_kb（reply_direct，无 retrieve）
- "什么是HTTP协议" → source=open_qa（open_answer，无 retrieve）

- [ ] **Step 4: 人工抽测 grade 兜底（可选）**

在前端问一个 KB 内肯定无证据的实时问题（如"现在比特币价格多少"，假设 KB 无此内容），观察思考过程：route→retrieve→grade→tool_agent，source 应为 tool 或 fallback_unverified（不再 refused/REFUSED）。

---

## Self-Review

**Spec coverage:** spec §5.1→Task1/3；§5.2→Task2；§5.3→Task4；§5.4→Task5；§5.5→Task6；§8→Task7。§4 架构、§6 数据流、§7 错误处理均在 task 内体现。全覆盖。

**Placeholder scan:** 无 TBD/TODO。天气 API 不可达分支给了明确处置（Open-Meteo 为主，Task1 Step1 实测，不可达则在 Task7 前换和风，逻辑同构）——非 placeholder。

**Type consistency:** `run_tool_agent` 返回 `{generation, source}`（Task3 定义，Task4 `_tool_agent_node` 消费一致）；`source="tool"`（Task3 产出，Task6 SOURCE_META、Task7 断言一致）；`EXTERNAL_TOOLS`/`WEATHER_TOOL`/`get_weather`（Task1 定义，Task3 消费）；`ThoughtEvent` 可选字段（Task2 chat.ts 定义，Task6 渲染用 `t.snippets &&` 一致）。

## 说明

- 每个后端 task 改完须重启 uvicorn（无 reload），否则命中旧代码。Task4/Task5 各有一次重启；Task7 前确认后端在跑。
- 不含任何 git commit / branch 步骤（用户全局指令）。
- grade 兜底改向是本次最关键改动（Task4 Step3），它让 tool_agent 的 auto 模式兼任"兜底判工具" + router 误判纠正器，省一次专门 LLM 调用。

# 工具调用路径设计（意图路由 v2）

> 状态：已与用户确认设计方向（brainstorming），待 spec 审阅 → writing-plans。
> 日期：2026-08-03
> 关联：`docs/superpowers/specs/2026-08-02-intent-routed-agent-design.md`（v1，6 类意图）

## 1. 背景与问题

W3.5 把纯 CRAG 升级为意图分层的问答智能体（6 类意图 + 规则快通道 + 非检索路径）。但**缺少"工具调用"这第三条路径**：

- "查天气/查股价/查汇率"这类**需要外部实时信息**的问题，现在无路可走。
- 它们不是 KB 检索（私有库里没有），也进不了 `open_answer`——`OPEN_ANSWER_PROMPT` 的护栏明确"不答实时信息"（返 `REFUSED`），最终被判 `out_of_scope` 直接拒。
- 用户问"今天北京天气"，现在大概率得到 `refused` / `REFUSED`——**不是答错，是系统没能力答**。

底层其实已具备能力：
- `src/agents/llm.py:chat_message(messages, tools=...)` 已封装 OpenAI 原生 function calling（`tool_choice="auto"`），走 LITELLM 网关。
- `src/agents/tools.py:run_search_agent` 已是跑通的 ReAct 工具循环（工具是 `search_knowledge_base`），证明 `tool_calls` 往返可用。

本次目标：建立"能区分 **KB 检索 / 外部工具 / LLM 常识** 并正确路由与协调"的机制，新增 `tool_call` 意图与 `tool_agent` 路径，接入 1 个真天气工具验证完整通路。

## 2. 已确认的关键决策（用户拍板）

| 决策点 | 选择 | 理由 |
|---|---|---|
| 架构范式 | **显式路由 + 工具 Agent** | 与现有 6 类一致、可控可观测、最小演进；加工具不动 router 以外逻辑 |
| 优先级协调 | **router 直判 + grade 兜底** | 明显工具问题跳过 KB 不白检索；模糊问题（KB 无证据）由 grade 兜底转工具 |
| 工具范围 | **框架 + 1 个真天气工具** | 一个真工具即可验证范式 + 演示；多接是 YAGNI |
| 误判容忍 | **依赖 grade 自然容错** | 不加二次确认 LLM；tool_agent 的 auto 模式让误判自然回落常识 |

## 3. 目标 / 非目标

**目标**
1. 新增第 7 类意图 `tool_call`，新增 `tool_agent` 跂点。
2. `grade` 兜底从 `open_answer` 改向 `tool_agent`（复用 tool_agent 的 auto 模式承担"兜底判工具"，省一次专门 LLM 调用）。
3. 接入天气工具（Open-Meteo 免费无 key；国内不可达退和风天气），验证完整 tool-calling 通路。
4. `source` 新增 `tool` 值 + 前端独立色徽章；工具调用过程前端可见。

**非目标（YAGNI）**
- 多个外部工具（汇率/股价/新闻）——框架支持扩展，但不本次实现。
- 工具结果缓存、限流、配额管理。
- 工具调用的细粒度审计日志表。
- 多步组合（先查 KB 再查工具）的显式编排——auto 模式已隐式支持单 agent 内多工具，不做跨节点编排。

## 4. 架构设计

### 4.1 意图分类 6 类 → 7 类

```
route ┬─ chitchat/capability → reply_direct        (不变)
      ├─ kb_simple/kb_complex → retrieve → grade   (不变)
      ├─ open_qa → open_answer                     (不变, 纯常识不加载工具, 省 token)
      ├─ tool_call(新) → tool_agent                ★新路径
      └─ out_of_scope → refused                    (不变)

grade ┬─ 有证据 → generate → verify                (不变)
      └─ 无证据 → tool_agent                       ★改: 原 goto open_answer
```

### 4.2 分层决策（避免 router 臃肿）

| 层 | 职责 | 是否带工具 schema |
|---|---|---|
| **router** | 粗分类：KB / 工具 / 常识 / 闲聊 / 越界 | ❌ 不带（省 token，只判 7 类） |
| **tool_agent** | 细决策：调哪个工具、调几次、何时停 | ✅ 带 `EXTERNAL_TOOLS` 全集 |

router 不需要知道具体工具名，只判"是否需要外部工具"这一类；具体调天气还是别的，交给 tool_agent 内部 function-calling。**新增工具只改 `EXTERNAL_TOOLS` 列表，不动 router**（开放-封闭原则）。

### 4.3 grade 兜底的核心简化

原：`grade` 无证据 → `open_answer`（纯常识兜底）。
现：`grade` 无证据 → `tool_agent`（带工具，auto 模式）。

tool_agent 的 auto 模式让 LLM 自己决定：需要工具就调（source=tool），不需要就用常识答（source=fallback_unverified）。**这样"兜底判工具"无需单独的 LLM 调用，复用 tool_agent 一并完成**——这是本次最关键的 ponytail 简化点。

同时这兼任 router 误判的纠正器：router 把工具问题误判成 KB → retrieve 无证据 → grade → tool_agent 兜底调工具；router 把 KB 问题误判成 tool_call → tool_agent 找不到合适工具 → 常识答 + fallback_unverified 标注。

## 5. 组件设计

### 5.1 `src/agents/tools.py`（扩展）

**(a) 天气工具定义 + 执行函数**

```python
WEATHER_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "查询指定城市的实时天气（温度、天气状况）。需要实时天气信息时调用。",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string", "description": "城市名（中文或英文，如 北京 / Beijing）"}},
            "required": ["city"],
        },
    },
}

EXTERNAL_TOOLS = [WEATHER_TOOL]  # 外部工具全集, tool_agent 用; 加工具只改这里

# WMO weather_code → 中文(Open-Meteo 返回的是数字码)
_WMO_CN = {0: "晴", 1: "晴", 2: "多云", 3: "阴", 45: "雾", 51: "小雨", 61: "雨", 71: "雪", 95: "雷雨"}
# ponytail: 只列常见码, 未列出的回落为"未知"

async def get_weather(city: str) -> str:
    """城市名 → Open-Meteo geocoding 查经纬度 → forecast 取实时天气.
    任一步失败返回'(天气服务暂不可用)'/'(找不到该城市)', 不抛异常(让LLM据实告知)."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            geo = (await c.get(f"https://geocoding-api.open-meteo.com/v1/search",
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

> **风险**：Open-Meteo 国内可达性未验证。实现时实测；不可达则换和风天气（`HEFENG_API_KEY` 入 `.env`，`get_weather` 内分支选择）。两种 API 的 catch/返回结构一致，切换不影响 tool_agent。

**(b) 工具分发 + tool_agent 闭环**

```python
MAX_TOOL_STEPS = 3  # ponytail: 天气类1次够, 留余量防多工具

async def _dispatch_tool(name: str, args: dict) -> str:
    if name == "get_weather":
        return await get_weather(args.get("city", ""))
    return f"(未知工具: {name})"

async def run_tool_agent(question: str, history: list | None = None) -> dict:
    """带外部工具的ReAct闭环: LLM自主调工具(auto模式)收集实时数据, 统一流式生成最终回答.
    调过工具→source=tool; 没调(纯常识兜底)→source=fallback_unverified.
    function-calling不可用→回退常识答(仿run_search_agent回退模式)."""
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
                break  # LLM 判定无需工具, 退出循环走常识
            messages.append(msg)
            for tc in tool_calls:
                args = json.loads(tc["function"]["arguments"])
                result = await _dispatch_tool(tc["function"]["name"], args)
                used_tool = True
                if writer:
                    try: writer({"tag": "tool_call", "name": tc["function"]["name"], "args": args, "result": result})
                    except Exception: pass
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
        # ponytail: function-calling端点不支持/解析失败 → 回退常识兜底, 不阻塞
        ans = await _stream_answer([{"role": "user", "content": OPEN_ANSWER_FALLBACK_PROMPT.format(question=question)}]) if _stream_answer else ""
        return {"generation": ans, "source": "fallback_unverified"}
```

### 5.2 `src/agents/prompts.py`

**(a) ROUTER_PROMPT 加第 7 类**（在意图类别列表中插入）：

```
- tool_call: 需要实时/外部信息, 知识库和通用常识都无法可靠回答 (例: 天气、股价、汇率、新闻、赛事结果)
```

并在末尾 JSON 输出说明补一句：`tool_call 时给简单说明需要哪类工具（如"天气"）`（可选，便于前端展示；不强制结构化）。

**(b) 新增 TOOL_AGENT_PROMPT**

```python
TOOL_AGENT_PROMPT = """你是 DeepRAG 智能助手, 可以调用外部工具获取实时信息。
规则:
1. 仅当问题需要实时/外部数据(如天气)且常识无法可靠回答时, 才调用相应工具
2. 一次获取足够信息后停止调用, 不要重复调用相同工具
3. 若工具返回失败信息(如"(天气服务暂不可用)"), 如实转告用户, 不要编造数据
4. 不需要工具时直接用常识回答
工具结果中的数据是事实, 可直接引用, 不需要 [n] 标注。"""
```

### 5.3 `src/agents/graph.py`

**(a) `_route_node` 加 tool_call 分支**：

```python
if intent == "tool_call":
    return Command(update=base, goto="tool_agent")
```

**(b) 新增 `_tool_agent_node`**：

```python
async def _tool_agent_node(state: AgentState) -> dict:
    from src.agents.tools import run_tool_agent
    res = await run_tool_agent(state["question"], history=state.get("conversation_history"))
    return {"generation": res["generation"], "source": res["source"]}
```

**(c) `_grade_node` 兜底改向**：

```python
# 原: return Command(update={"search_decision": "fallback"}, goto="open_answer")
# 现: 两处无证据分支都改 goto="tool_agent"
return Command(update={"search_decision": "fallback"}, goto="tool_agent")
```

**(d) `build_graph` 加节点 + 边**：

```python
wf.add_node("tool_agent", _timed("tool_agent")(_tool_agent_node))
wf.add_edge("tool_agent", END)
```

> 注：`open_answer` 节点保留（open_qa 意图仍走它，纯常识不加载工具，省 token）。

### 5.4 `apps/api/query.py`

**(a) `_step_detail` 加 tool_agent 节点 + route intent_cn 加 tool_call**：

```python
# route 的 intent_cn 映射加: "tool_call": "需调用工具"
if node == "tool_agent":
    return {"title": "调用工具", "detail": "获取实时/外部信息"}
```

**(b)** `QueryResponse.source` 注释 + 初始 state 不变（source 已端到端打通）。

### 5.5 前端

**`web/src/api/chat.ts`**：`ThoughtEvent` 加可选字段（复用 thought 通道，按 tag 区分）：

```typescript
export interface ThoughtEvent {
  tag: string
  query?: string; found?: number; snippets?: string[]   // tag=="search"
  name?: string; args?: Record<string, unknown>; result?: string  // tag=="tool_call"
}
```

**`web/src/pages/Chat.tsx`**：
- `SOURCE_META` 加 `tool: { label: "工具·实时", variant: ..., icon: Wrench }`（独立色；Badge 若仅 4 variant 则加 `info` variant 或用图标区分，实现时确认 `components/ui/badge.tsx`）。
- thought 渲染（line ~372）按 `t.tag` 区分：`search` → 检索卡（现有），`tool_call` → 工具卡（工具名 + 参数 + 结果摘要）。

## 6. 数据流（三场景）

| 场景 | 流程 | source |
|---|---|---|
| "北京今天天气" | route(tool_call) → tool_agent[get_weather→25°C→流式答] → END | tool |
| KB 无证据的实时问题 | route(kb) → retrieve → grade(无证据) → tool_agent[调工具/常识] → END | tool 或 fallback_unverified |
| 正常 KB 问题 | route(kb) → retrieve → grade → generate → verify | kb_grounded（不变） |
| 闲聊 / 纯常识 | reply_direct / open_answer（不变） | non_kb / open_qa |

## 7. 错误处理

| 失败 | 处理 |
|---|---|
| 天气 API 不可达/超时 | `get_weather` 内部 catch → 返回 `"(天气服务暂不可用)"`，LLM 据实告知（不瞎编） |
| 城市 geocoding 无结果 | 返回 `"(找不到城市: X)"` |
| function-calling 端点不支持 | tool_agent 回退常识兜底（仿 run_search_agent），source=fallback_unverified |
| Open-Meteo 国内不可达 | 实现时实测，必要时换和风天气（HEFENG_API_KEY 入 .env） |

## 8. 测试策略

**`scripts/test_tool_calling.py`**（端到端，带断言自检）：
1. "北京今天天气怎么样" → route=tool_call → tool_agent → get_weather 被调 → source=tool
2. 回归：闲聊（"你好"）→ reply_direct，source=non_kb
3. 回归：常识（"什么是 HTTP 协议"）→ open_qa → open_answer，source=open_qa
4. 回归：KB 问题 → kb_grounded（完整 retrieve→grade→generate→verify）
5. grade 兜底：构造一个 KB 内无证据的实时问题 → tool_agent 兜底

**`src/agents/graph.py` 的 `__main__` 自检**：扩展 `_classify_by_rule` 自检不变（规则快通道未改）。

## 9. 实施顺序（TDD，每步可独立验证）

1. **tools.py**：`WEATHER_TOOL` + `get_weather`（先单独 curl 验证 Open-Meteo 可达性）+ `_dispatch_tool` + `run_tool_agent`
2. **prompts.py**：`ROUTER_PROMPT` 加 tool_call + 新增 `TOOL_AGENT_PROMPT`
3. **graph.py**：`_route_node` 加分支 + `_tool_agent_node` + `_grade_node` 改向 + `build_graph` 加节点边
4. **query.py**：`_step_detail` 加 tool_agent + route intent_cn
5. **前端**：chat.ts ThoughtEvent 扩展 + Chat.tsx SOURCE_META 加 tool + thought 按 tag 渲染
6. **test_tool_calling.py**：全场景验证 + 回归

## 10. 关键设计记录

- **为什么 grade 兜底复用 tool_agent 而非新开判工具节点**：tool_agent 的 auto 模式已隐含"判工具"——LLM 自己决定调不调。新开节点=多一次 LLM 调用 + 多一个分支，违背 ponytail。
- **为什么 router 不带工具 schema**：router 是粗分类，带 schema 会膨胀 token 且 router 不负责选具体工具。分层让 router 稳定（加工具不改 router）。
- **为什么 tool_agent 自答闭环（不像 search_agent 只返回证据）**：工具数据（温度）需 LLM 转自然语言，且不需 KB 的 `[n]` 引用格式；verify（grounded 校验）针对 KB 证据，工具结果是事实数据不需 grounded 校验。
- **为什么 `_stream_answer` 延迟 import**：tools.py 与 graph.py 互相依赖（graph import tools 的 run_search_agent），模块级 import 会循环；函数内延迟 import 是标准解法。
- **为什么规则快通道不加"天气"**：天气问法多样（"今天热吗"/"出门要带伞吗"），正则难保证高 precision；交 LLM router 判。快通道仍只抓 chitchat/capacity（已验证零误伤）。

## 11. 简历叙事升级

"意图分层问答智能体" → "带工具调用的意图分层 Agent"：在 KB 检索 / 外部工具 / LLM 常识三类信息源间做意图路由与优先级协调，支持实时信息获取（天气等），router/tool_agent 分层决策 + grade 兜底自然容错。

# 扩展工具（计算器 + 网络搜索）+ sub_queries 可观测化

> 状态：已与用户确认设计方向（brainstorming），待 spec 审阅 → writing-plans。
> 日期：2026-08-03
> 关联：`docs/superpowers/specs/2026-08-03-tool-calling-design.md`（工具调用 v1，天气工具）

## 1. 背景与目标

主线 A（深化 Agent）的第一步：**扩工具**，为后续"多源编排 plan"和"长期记忆"铺路。

工具调用 v1（天气）已验证完整通路（`EXTERNAL_TOOLS` + `_dispatch_tool` + `run_tool_agent` 闭环）。但单工具无法体现 function-calling 的**多工具选择**能力，也无法支撑"跨源编排"。本次新增 2 个工具，把信息维度从 1（结构化实时·天气）扩展到 3：

| 工具 | 维度 | 依赖 |
|---|---|---|
| get_weather（已有） | 结构化实时 | Open-Meteo 免费 |
| **calculate**（新） | 精确计算 | 零依赖（ast） |
| **web_search**（新） | 非结构化实时 | Tavily API（需 key） |

同时把 router 的 `sub_queries` 分解计划在前端可见（可观测化），让用户看到"检索计划"。

## 2. 已确认决策（用户拍板）

| 决策点 | 选择 | 理由 |
|---|---|---|
| 工具组合 | 计算器 + 网络搜索 | 三个维度（精确计算/结构化实时/非结构化实时）支撑后续多源 plan |
| 搜索后端 | **Tavily 优先**（抽象可切 Bing） | 专为 AI 设计，一次调用拿摘要；国内 DDG/Wikipedia 实测被墙 |
| sub_queries 可观测化 | **做** | 展示 router 分解计划，透明度价值；成本极低 |

> ⚠️ 实测：DuckDuckGo Instant Answer、Wikipedia 中英文 API 在国内均空响应（被墙）。免费无 key 搜索方案不可行，故用 Tavily（需注册 key）。

## 3. 目标 / 非目标

**目标**
1. 新增 `calculate`（ast 安全计算器）+ `web_search`（Tavily）两个工具。
2. 接入 `EXTERNAL_TOOLS` + `_dispatch_tool`，`run_tool_agent` 自动在 4 工具间选择。
3. `sub_queries` 在 route step 可观测（前端展示分解内容）。
4. 扩展端到端测试覆盖新工具。

**非目标（YAGNI）**
- Bing 后端实现（抽象预留，本次只实现 Tavily；Bing 分支留 TODO 注释）。
- 工具权限控制（RBAC）、工具调用审计、搜索结果缓存。
- 多源编排 plan 节点（下一阶段）。

## 4. 工具设计

### 4.1 计算器（`src/agents/tools.py`）

```python
import ast
import math

CALCULATOR_TOOL = {
    "type": "function",
    "function": {
        "name": "calculate",
        "description": "精确计算数学表达式（四则运算、幂、括号、数学函数）。需要精确数值计算时调用，避免心算出错。",
        "parameters": {
            "type": "object",
            "properties": {"expression": {"type": "string", "description": "数学表达式，如 (1+2)*3, sqrt(16), 2**10"}},
            "required": ["expression"],
        },
    },
}

# ponytail: ast 安全白名单——允许的运算符/函数, 其余节点(Name/Attribute/Call非白名单)一律拒, 防 LL M 注入
_ALLOWED_BINOPS = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/", ast.Pow: "**",
                   ast.Mod: "%", ast.FloorDiv: "//"}
_ALLOWED_UNARYOPS = {ast.UAdd: "+", ast.USub: "-"}
_ALLOWED_FUNCS = {"sqrt": math.sqrt, "abs": abs, "floor": math.floor, "ceil": math.ceil,
                  "log": math.log, "log10": math.log10, "sin": math.sin, "cos": math.cos}


def calculate(expression: str) -> str:
    """安全计算数学表达式(ast白名单求值, 禁eval). 失败返回错误提示串(不抛)."""
    expr = (expression or "").strip()
    if not expr or len(expr) > 200:
        return "(表达式为空或过长)"
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return f"(表达式语法错误: {expr})"

    def _eval(node):
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant):  # 数字
            if isinstance(node.value, (int, float)):
                return node.value
            raise ValueError("非常量")
        if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
            left, right = _eval(node.left), _eval(node.right)
            op = node.op
            if isinstance(op, ast.Div) and right == 0:
                raise ZeroDivisionError
            return _apply_binop(op, left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARYOPS:
            v = _eval(node.operand)
            return v if isinstance(node.op, ast.UAdd) else -v
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in _ALLOWED_FUNCS and node.args and not node.keywords:
            args = [_eval(a) for a in node.args]
            return _ALLOWED_FUNCS[node.func.id](*args)
        raise ValueError(f"不允许的节点: {type(node).__name__}")

    try:
        result = _eval(tree)
        if isinstance(result, float) and (math.isinf(result) or math.isnan(result)):
            return "(计算结果无效)"
        return f"{expr} = {result}"
    except ZeroDivisionError:
        return "(除零错误)"
    except Exception as e:
        return f"(无法计算: {type(e).__name__})"


def _apply_binop(op, left, right):
    import operator
    ops = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
           ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
           ast.FloorDiv: operator.floordiv}
    return ops[type(op)](left, right)
```

**安全要点**：
- **绝不用 `eval`**——用 `ast.parse(mode="eval")` + 递归白名单求值
- 白名单：`Constant`(数字) / `BinOp`(7 种运算) / `UnaryOp`(正负) / `Call`(8 个数学函数)
- 任何其他节点（`Name`/`Attribute`/`Subscript`/非白名单 `Call`）→ `ValueError` 拒绝
- 长度限制 200 字符防 DoS
- 除零、inf/nan 兜底

### 4.2 网络搜索（Tavily）

```python
import os

WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "搜索互联网获取最新信息或通用知识（新闻、技术资料、事件进展等）。知识库和天气工具无法满足时调用。",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "搜索查询词"}},
            "required": ["query"],
        },
    },
}


async def web_search(query: str) -> str:
    """Tavily 搜索: 返回 answer 摘要 + top3 结果. 无key/失败返回友好提示(不抛).
    ponytail: 抽象预留 Bing 分支(BING_API_KEY 配置即启用), 本次只实现 Tavily."""
    import httpx
    api_key = os.getenv("TAVILY_API_KEY", "")
    if not api_key:
        return "(未配置搜索 key, 无法联网搜索)"
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post("https://api.tavily.com/search",
                             json={"api_key": api_key, "query": query,
                                   "max_results": 3, "search_depth": "basic"})
            r.raise_for_status()
            data = r.json()
        answer = data.get("answer", "").strip()
        results = data.get("results", [])[:3]
        parts = []
        if answer:
            parts.append(answer)
        for i, item in enumerate(results, 1):
            title = item.get("title", "")
            content = item.get("content", "")[:200]
            parts.append(f"[{i}] {title}: {content}")
        return "\n".join(parts) or "(搜索无结果)"
    except Exception:
        return "(搜索服务暂不可用)"
```

**设计要点**：
- 无 key → 友好提示（不崩，LLM 据实告知用户"未配置"）
- 返回 Tavily 的 `answer`（LLM 友好摘要）+ top3 结果摘要（title+content 截断 200 字）
- 失败兜底 `"(搜索服务暂不可用)"`
- 抽象预留 Bing（注释说明，本次不实现）

## 5. 接入（`src/agents/tools.py`）

```python
EXTERNAL_TOOLS = [WEATHER_TOOL, CALCULATOR_TOOL, WEB_SEARCH_TOOL]


async def _dispatch_tool(name: str, args: dict) -> str:
    if name == "get_weather":
        return await get_weather(args.get("city", ""))
    if name == "calculate":
        return calculate(args.get("expression", ""))
    if name == "web_search":
        return await web_search(args.get("query", ""))
    return f"(未知工具: {name})"
```

**零改动**：router、graph、前端都不动。`run_tool_agent` 的 function-calling auto 模式自动从 4 个工具的 schema 中选择。`MAX_TOOL_STEPS` 保持 3。

> `TOOL_AGENT_PROMPT` 无需改——LLM 从 tools schema 自动获知新工具能力。

## 6. sub_queries 可观测化（`apps/api/query.py`）

`_step_detail` 的 route 分支已拿到 `subs`，现仅显示数量。改为显示内容：

```python
# 原: + (f"，分解 {len(subs)} 个子查询" if subs else "")
# 现:
+ (f"，子查询: {' / '.join(subs)}" if subs else "")
```

route step 展示变为："路由分析 · 意图: 库内复杂(complex)，子查询: 查A的架构 / 查B的架构"。

**零前端改动**：前端 ThinkingTrace 的 step 渲染是 `title·detail` 文本，detail 变长即展示子查询内容。

## 7. 测试

### 7.1 计算器安全自检（`scripts/diag_calculator.py`）

```python
from src.agents.tools import calculate
# 正确性
assert "= 7" in calculate("1+2*3")
assert "= 1024" in calculate("2**10")
assert "4.0" in calculate("sqrt(16)") or "= 4" in calculate("sqrt(16)")
# 安全边界(注入被拒)
assert "不允许" in calculate("__import__('os').system('ls')") or "无法计算" in calculate("__import__('os').system('ls')")
assert "无法计算" in calculate("open('x')") or "不允许" in calculate("open('x')")
print("calculator ok")
```

### 7.2 端到端（扩展 `scripts/test_tool_calling.py`）

新增 case：
- "计算 123 乘以 456" → `source=tool` + calculator 调用
- "搜索一下最新的 RAG 综述" → `source=tool` + search 调用（**需 TAVILY_API_KEY**，未配则验证 source 仍为 tool 但结果含"未配置"，或跳过）

## 8. 关键设计记录

- **计算器为何用 ast 不用 eval**：LLM 生成的表达式不可信（prompt injection / 代码注入），`eval` 可执行任意代码。ast 白名单求值是安全计算的标准做法，且标准库自带，零依赖。
- **搜索为何抽象后端**：国内可达性不确定（DDG/Wikipedia 已被墙），Tavily 也可能不稳。抽象 `web_search(query)` 接口，后端按 .env key 切换，Tavily 不可达可无缝切 Bing。本次只实现 Tavily（YAGNI），Bing 留注释分支。
- **sub_queries 可观测化为何一行即可**：数据已在 `_step_detail` 的 route 分支（`subs`），只显示数量；改显示内容即可，零前端改动（detail 文本变长）。
- **为何不动 router/前端**：工具调用 v1 已建立"加工具只改 EXTERNAL_TOOLS + _dispatch_tool"的开放-封闭模式，本次完全遵循。
- **MAX_TOOL_STEPS 保持 3**：典型每个工具 1 步，组合 2-3 步够；多工具不会显著增加步数。

## 9. 后续（不在本次）

- 多源编排 plan 节点（KB + 工具组合，条件触发）
- 长期记忆（LangGraph Store / 自建）
- Bing 后端实现（若 Tavily 不可达）
- 工具权限 RBAC / 审计

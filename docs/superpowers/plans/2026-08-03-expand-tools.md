# 扩展工具（计算器 + 网络搜索）+ sub_queries 可观测化 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 tool_agent 新增 2 个工具（计算器 + Tavily 网络搜索），扩展信息维度；并把 router 的 sub_queries 在前端可见。为后续多源编排 plan / 长期记忆铺路。

**Architecture:** 沿用工具调用 v1 的开放-封闭模式——加工具只改 `EXTERNAL_TOOLS` + `_dispatch_tool` + 执行函数，不动 router/前端。计算器用 ast 白名单安全求值（禁 eval）；搜索 Tavily 抽象后端（可切 Bing）。

**Tech Stack:** Python ast（标准库，计算器安全求值）、httpx（Tavily）、TAVILY_API_KEY（.env）。

**设计依据:** `docs/superpowers/specs/2026-08-03-expand-tools-design.md`（已审阅）。

## Global Constraints

- **简体中文**：回复 + 代码注释一律中文。
- **禁止 git 操作**：用户全局指令——绝对不要 git commit / branch / add。每 task 以"验证通过"收尾。
- **.env 不提交**：Tavily key（`TAVILY_API_KEY`）入 .env，不入版本库。
- **ponytail full**：标准库优先（ast 计算器零依赖）、最短差异、`ponytail:` 标记简化。
- **后端无 reload**：改后端代码须重启。重启前 `netstat -ano | grep :8000 | grep LISTENING` 抓 PID → `taskkill //F //T //PID <pid>`，再 `python -m apps.api.main`（后台）。
- **tsc 验证用绝对路径**：`node "E:/DeepRAG/web/node_modules/typescript/bin/tsc" --noEmit -p "E:/DeepRAG/web/tsconfig.json"`。本次前端零改动，tsc 非必需。
- **Tavily key 前置**：Task 4 端到端测试的搜索 case 需 `TAVILY_API_KEY`。用户应在测试前注册（tavily.com，免费 1000/月）放 .env；未配则搜索工具返回"未配置"兜底（测试验证兜底路径）。

## File Structure

| 文件 | 责任 | 动作 |
|---|---|---|
| `src/agents/tools.py` | 加 CALCULATOR_TOOL/calculate + WEB_SEARCH_TOOL/web_search；扩 EXTERNAL_TOOLS/_dispatch_tool | 修改 |
| `apps/api/query.py` | _step_detail route 分支显示 sub_queries 内容 | 修改 |
| `scripts/diag_calculator.py` | 计算器安全边界自检 | 新建 |
| `scripts/test_tool_calling.py` | 扩展计算/搜索 case | 修改 |

---

### Task 1: 计算器（tools.py + 自检）

**Files:**
- Modify: `src/agents/tools.py`（顶部 import 加 ast/math；加 CALCULATOR_TOOL + 白名单 + calculate + _apply_binop）
- Test: `scripts/diag_calculator.py`（新建）

**Interfaces:**
- Consumes: 标准库 `ast`/`math`/`operator`
- Produces: `CALCULATOR_TOOL`、`calculate(expression: str) -> str`

- [ ] **Step 1: tools.py 顶部 import 加 ast, math**

把 `import json` 那行扩展为：
```python
import json
import ast
import math
```

- [ ] **Step 2: 加 CALCULATOR_TOOL + 白名单 + calculate + _apply_binop**

在 `get_weather` 函数之后、`run_search_agent` 之前插入：

```python
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

# ponytail: ast安全白名单——允许的运算符/函数, 其余节点(Name/Attribute/非白名单Call)一律拒, 防 LLM 注入
_ALLOWED_BINOPS = {ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod, ast.FloorDiv}
_ALLOWED_UNARYOPS = {ast.UAdd, ast.USub}
_ALLOWED_FUNCS = {"sqrt": math.sqrt, "abs": abs, "floor": math.floor, "ceil": math.ceil,
                  "log": math.log, "log10": math.log10, "sin": math.sin, "cos": math.cos}


def calculate(expression: str) -> str:
    """安全计算数学表达式(ast白名单求值, 禁eval). 失败返回错误提示串(不抛)."""
    import operator
    expr = (expression or "").strip()
    if not expr or len(expr) > 200:
        return "(表达式为空或过长)"
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return f"(表达式语法错误: {expr})"

    _binop_map = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
                  ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
                  ast.FloorDiv: operator.floordiv}

    def _eval(node):
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
            left, right = _eval(node.left), _eval(node.right)
            if isinstance(node.op, ast.Div) and right == 0:
                raise ZeroDivisionError
            return _binop_map[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARYOPS:
            v = _eval(node.operand)
            return v if isinstance(node.op, ast.UAdd) else -v
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in _ALLOWED_FUNCS and node.args and not node.keywords:
            return _ALLOWED_FUNCS[node.func.id](*[_eval(a) for a in node.args])
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
```

- [ ] **Step 3: 写自检脚本 `scripts/diag_calculator.py`**

```python
from src.agents.tools import calculate

def main():
    # 正确性
    assert "= 7" in calculate("1+2*3"), calculate("1+2*3")
    assert "= 1024" in calculate("2**10"), calculate("2**10")
    assert "4" in calculate("sqrt(16)"), calculate("sqrt(16)")
    assert "= 1.5" in calculate("3/2"), calculate("3/2")
    # 安全边界: 注入表达式必须被拒(返回错误提示, 不执行)
    for inj in ["__import__('os').system('ls')", "open('x')", "os.system('ls')", "1+1;import os"]:
        r = calculate(inj)
        assert "无法计算" in r or "语法错误" in r, f"注入未被拒: {inj} -> {r}"
    # 边界: 除零/空/超长
    assert "除零" in calculate("1/0")
    assert "为空" in calculate("")
    print("calculator ok")

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 跑自检**

Run: `cd /e/DeepRAG && python scripts/diag_calculator.py`
Expected: `calculator ok`（注入全被拒、运算正确、边界兜底）。

---

### Task 2: 网络搜索（tools.py）

**Files:**
- Modify: `src/agents/tools.py`（加 WEB_SEARCH_TOOL + web_search）

**Interfaces:**
- Consumes: `httpx`、`os`（标准库）、`TAVILY_API_KEY`（.env）
- Produces: `WEB_SEARCH_TOOL`、`web_search(query: str) -> str`

- [ ] **Step 1: 加 WEB_SEARCH_TOOL + web_search**

在 `calculate` 函数之后插入：

```python
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
    """Tavily搜索: 返回answer摘要+top3结果. 无key/失败返回友好提示(不抛).
    ponytail: 抽象预留Bing分支(BING_API_KEY配置即启用), 本次只实现Tavily."""
    import os, httpx
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
        answer = (data.get("answer") or "").strip()
        results = data.get("results", [])[:3]
        parts = []
        if answer:
            parts.append(answer)
        for i, item in enumerate(results, 1):
            parts.append(f"[{i}] {item.get('title','')}: {item.get('content','')[:200]}")
        return "\n".join(parts) or "(搜索无结果)"
    except Exception:
        return "(搜索服务暂不可用)"
```

- [ ] **Step 2: 验证 import 与无 key 兜底**

Run: `cd /e/DeepRAG && python -c "import asyncio; from src.agents.tools import web_search; print(asyncio.run(web_search('test')))"`
Expected: `(未配置搜索 key, 无法联网搜索)`（无 key 时的兜底，证明函数不崩）。
若已配 `TAVILY_API_KEY`：应返回含 answer/结果的真实搜索内容。

---

### Task 3: 接入工具注册 + sub_queries 可观测化

**Files:**
- Modify: `src/agents/tools.py`（EXTERNAL_TOOLS + _dispatch_tool）
- Modify: `apps/api/query.py`（_step_detail route 分支）

**Interfaces:**
- Consumes: Task 1 `calculate`、Task 2 `web_search`、现有 `get_weather`
- Produces: `EXTERNAL_TOOLS` 4 工具、`_dispatch_tool` 3 分支；route step 含 sub_queries 内容

- [ ] **Step 1: 扩展 EXTERNAL_TOOLS**

```python
EXTERNAL_TOOLS = [WEATHER_TOOL, CALCULATOR_TOOL, WEB_SEARCH_TOOL]
```

- [ ] **Step 2: 扩展 _dispatch_tool**

```python
async def _dispatch_tool(name: str, args: dict) -> str:
    """工具名→执行函数分发. 未知工具返回提示串(不抛)."""
    if name == "get_weather":
        return await get_weather(args.get("city", ""))
    if name == "calculate":
        return calculate(args.get("expression", ""))
    if name == "web_search":
        return await web_search(args.get("query", ""))
    return f"(未知工具: {name})"
```

- [ ] **Step 3: sub_queries 可观测化（query.py _step_detail）**

把 `apps/api/query.py` 的 `_step_detail` route 分支里的：
```python
        detail = f"意图: {intent_cn}" + (f"({cx})" if it.startswith("kb") else "") + (f"，分解 {len(subs)} 个子查询" if subs else "")
```
改为：
```python
        detail = f"意图: {intent_cn}" + (f"({cx})" if it.startswith("kb") else "") + (f"，子查询: {' / '.join(subs)}" if subs else "")
```

- [ ] **Step 4: 重启后端 + 验证**

```bash
PID=$(netstat -ano | grep :8000 | grep LISTENING | awk '{print $NF}'); [ -n "$PID" ] && taskkill //F //T //PID $PID
python -m apps.api.main &   # 后台启动
```
Run: `cd /e/DeepRAG && python -c "from apps.api.query import _step_detail; print(_step_detail('route', {'intent':'kb_complex','complexity':'complex','sub_queries':['查A架构','查B架构']}))"`
Expected: `{'title': '路由分析', 'detail': '意图: 库内复杂(complex)，子查询: 查A架构 / 查B架构'}`。

---

### Task 4: 端到端测试（扩展 test_tool_calling.py）

**Files:**
- Modify: `scripts/test_tool_calling.py`

**Interfaces:**
- Consumes: Task 1-3 全部产出；`/api/v1/query/stream`

- [ ] **Step 1: 扩展 cases**

在 `test_tool_calling.py` 的 `cases` 列表中新增（保留原 3 个回归 case）：

```python
    cases = [
        ("北京今天天气怎么样", "tool", True, True, "工具·天气"),      # 原有
        ("你好", "non_kb", False, True, "闲聊回归"),                  # 原有
        ("什么是HTTP协议", "open_qa", False, True, "常识回归"),        # 原有
        ("计算 123 乘以 456", "tool", True, True, "工具·计算器"),      # 新增
        ("帮我搜索一下最新的 RAG 综述", "tool", True, True, "工具·搜索"),  # 新增(需 TAVILY_API_KEY)
    ]
```

> 注：`stream()` 已检测 `tag=="tool_call"` thought，新工具的调用会自动被 `saw_tool` 捕获（计算器/搜索都走 `tool_call` thought）。

- [ ] **Step 2: 确保后端运行 + Tavily key 已配**

```bash
netstat -ano | grep :8000 | grep LISTENING   # 无输出则启动
# 确认 .env 含 TAVILY_API_KEY=tvly-...（搜索 case 联网所需）
```

- [ ] **Step 3: 跑测试**

Run: `cd /e/DeepRAG && PYTHONIOENCODING=utf-8 python scripts/test_tool_calling.py`
Expected: 5 个 case 全 `[PASS]` + `test_tool_calling all pass`。
- "计算 123 乘以 456" → `source=tool` + 出现 tool_call（calculator）
- "搜索最新的 RAG 综述" → `source=tool` + 出现 tool_call（web_search）；**未配 key 时**：source 仍为 tool（LLM 调了搜索工具），但若 Tavily 返回失败，LLM 会转告"未配置/不可用"——此时若 case 因搜索结果不稳失败，可临时注释该 case 或接受 source=tool 即 pass

- [ ] **Step 4: 人工抽测多工具选择**

在前端问：
- "北京天气怎么样，再帮我算下 12×15" → 应看到 tool_call 出现 get_weather + calculate 两个工具（验证 auto 模式多工具选择 + MAX_TOOL_STEPS=3 容纳）

---

## Self-Review

**Spec coverage:** spec §4.1→Task1；§4.2→Task2；§5→Task3 Step1-2；§6→Task3 Step3；§7→Task1自检+Task4。全覆盖。

**Placeholder scan:** 无 TBD/TODO。Bing 分支是明确 YAGNI（spec §9 + web_search 注释说明本次不实现），非 placeholder。

**Type consistency:** `calculate(expression)->str`（Task1 定义，Task3 _dispatch_tool 消费）；`web_search(query)->str`（Task2 定义，Task3 消费）；`EXTERNAL_TOOLS` 4 工具（Task3 Step1，Task4 测试隐式依赖）；`tool_call` thought tag（test_tool_calling 已检测，新工具自动覆盖）。

**安全验证:** Task1 自检含 4 种注入表达式（`__import__`/`open`/`os.system`/分号注入），全须被 ast 拒。

## 说明

- Task 1-3 全在 tools.py + query.py，无前端改动，无 tsc。
- 计算器是唯一有安全风险的点（LLM 生成表达式）——ast 白名单是核心，自检必须覆盖注入。
- 搜索 case 依赖 Tavily key；未配则工具返回兜底提示，端到端 source 仍为 tool（LLM 调用了工具）。
- 后端改代码（Task3）须重启加载；Task1/2 改 tools.py 也需后端重启才在 API 生效（test_tool_calling 经 API）。

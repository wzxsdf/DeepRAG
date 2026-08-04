# ponytail: Agent工具层 - search_knowledge_base工具 + ReAct多轮检索
# LLM自主决定调用检索(可多次, 自拟查询), OpenAI function-calling原生实现, 不引入langchain

import json
import ast
import math
from langgraph.config import get_stream_writer
from src.agents.llm import chat_message
from src.retrieval.reranker import rerank
from src.storage.protocol import RetrievalFilter

SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "search_knowledge_base",
        "description": "在知识库检索与查询相关的文档片段。需要查找事实证据时调用,可多次调用不同查询(分解子问题/换说法)以提升召回。",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "检索查询(关键词或问句)"}},
            "required": ["query"],
        },
    },
}

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
            parts.append(f"[{i}] {item.get('title', '')}: {item.get('content', '')[:200]}")
        return "\n".join(parts) or "(搜索无结果)"
    except Exception:
        return "(搜索服务暂不可用)"


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


# ponytail: quick/agent常态共用工具集(含KB检索), 取代原EXTERNAL_TOOLS; 开放-封闭, 加工具只改这里
# 放在所有工具定义之后, 避免前向引用 NameError
REACT_TOOLS = [SEARCH_TOOL, WEATHER_TOOL, CALCULATOR_TOOL, WEB_SEARCH_TOOL, TIME_TOOL]


async def run_search_agent(retriever, question: str, tenant_id: str = "default",
                           hints: list[str] | None = None, kb_ids: list[str] | None = None,
                           max_searches: int = 4):
    """ReAct工具调用检索: LLM自主调用search收集证据, 去重融合后rerank. 失败回退单次检索.

    max_searches: 搜索总次数硬上限(跨回合累计; 一回合可发多个tool_call). 适当保留多跳能力, 防过度召回.
    hints: route给出的建议子查询(作为初始线索, 可None).
    kb_ids: 限定检索的知识库列表(None=不限).
    返回: list[RetrievedDocument] (已rerank).
    """
    sys_msg = ("你是检索Agent。用 search_knowledge_base 工具收集足够证据来回答问题。"
               "通常 2-3 次搜索即可覆盖要点, 证据充分就停止, 不要为追求召回而重复或过度搜索。"
               "每次工具返回证据摘要后, 判断是否还需补充。收集充分后回复一句中文小结(不要直接答题)。")
    if hints:
        sys_msg += f"\n建议优先尝试的子查询: {json.dumps(hints, ensure_ascii=False)}"
    messages = [{"role": "system", "content": sys_msg}, {"role": "user", "content": question}]

    # ponytail: 取流式writer(非流式上下文如eval的ainvoke下为no-op, 不影响), 用于向前端推送检索推理
    try:
        writer = get_stream_writer()
    except Exception:
        writer = None

    acc: dict[str, object] = {}  # chunk_id -> doc, 去重保留最高分
    searches = 0  # ponytail: 已发起搜索次数(跨回合累计), 到max_searches即停, 防过度召回(原仅限回合数不限tool_call数→实测6次)
    try:
        while searches < max_searches:
            msg = await chat_message(messages, tools=[SEARCH_TOOL])
            tool_calls = msg.get("tool_calls")
            if not tool_calls:
                break  # Agent判定证据充分, 退出循环
            messages.append(msg)
            for tc in tool_calls:
                if searches >= max_searches:
                    break  # 到达搜索上限, 停止本轮剩余tool_call
                q = json.loads(tc["function"]["arguments"]).get("query", question)
                docs = await retriever.retrieve(q, RetrievalFilter(tenant_id=tenant_id, kb_ids=kb_ids), top_k=5)
                searches += 1
                for d in docs:
                    if d.chunk_id not in acc or d.score > acc[d.chunk_id].score:
                        acc[d.chunk_id] = d
                # 推送思考事件: Agent检索了什么query、找到几条、摘要(豆包式推理过程展示)
                if writer:
                    try:
                        writer({"tag": "search", "query": q, "found": len(docs),
                                "snippets": [d.content[:80] for d in docs[:3]]})
                    except Exception:
                        pass
                # ponytail: 只回喂内容摘要(前120字), 避免context爆炸; Agent据摘要决定是否续检
                snippet = "\n".join(f"- {d.content[:120]}" for d in docs[:3]) or "(无结果)"
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": snippet})
    except Exception as e:
        # ponytail: 工具调用不可用(端点不支持tools/解析失败) → 回退单次检索, 不阻塞主流程
        print(f"[search_agent] 回退单次检索: {type(e).__name__}: {e}")
        docs = await retriever.retrieve(question, RetrievalFilter(tenant_id=tenant_id, kb_ids=kb_ids), top_k=5)
        return await rerank(question, docs, top_n=5)

    docs = list(acc.values())
    if not docs:  # Agent未触发任何search → 兜底用原问题检索一次
        docs = await retriever.retrieve(question, RetrievalFilter(tenant_id=tenant_id, kb_ids=kb_ids), top_k=5)
    return await rerank(question, docs, top_n=5)


MAX_TOOL_STEPS = 3  # ponytail: 天气类1次够, 留余量防多工具过度调用


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


async def run_tool_agent(retriever, question: str, tenant_id: str = "default",
                         kb_ids: list[str] | None = None, history: list | None = None) -> dict:
    """带工具的ReAct闭环(含KB检索+外部工具): LLM自主调工具收集信息, 流式生成回答.
    调过工具→source=tool; 没调(纯常识兜底)→source=fallback_unverified.
    ponytail: function-calling不可用→回退常识兜底, 不阻塞主流程."""
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
            msg = await chat_message(messages, tools=REACT_TOOLS)
            tool_calls = msg.get("tool_calls")
            if not tool_calls:
                break  # LLM判定无需工具, 退出走常识
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


if __name__ == "__main__":
    s = get_time()
    assert "周" in s and len(s) > 15, f"get_time 输出异常: {s}"
    print(f"get_time ok: {s}")

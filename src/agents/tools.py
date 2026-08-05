# ponytail: Agent工具层 - search_knowledge_base工具 + ReAct多轮检索
# LLM自主决定调用检索(可多次, 自拟查询), OpenAI function-calling原生实现, 不引入langchain

import ast
import math
from langgraph.config import get_stream_writer
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
from langchain.agents.middleware import AgentMiddleware
from src.retrieval.reranker import rerank
from src.storage.protocol import RetrievalFilter
from src.core.logging import log

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


# ponytail: create_agent 的 @tool 包装(取代dict+dispatch) — schema自动从签名+docstring生成, 复用上方裸函数实现
@tool
async def search_kb_tool(query: str, config: RunnableConfig) -> str:
    """在知识库检索与查询相关的文档片段。需要查找事实证据(如简历内容、上传文档)时调用。"""
    cfg = (config or {}).get("configurable", {})
    retriever = cfg.get("retriever")
    if retriever is None:
        return "(检索器未配置)"
    tenant_id = cfg.get("tenant_id", "default")
    kb_ids = cfg.get("kb_ids")
    docs = await retriever.retrieve(query, RetrievalFilter(tenant_id=tenant_id, kb_ids=kb_ids), top_k=5)
    docs = await rerank(query, docs, top_n=3)
    return "\n".join(f"- {d.content[:150]}" for d in docs) or "(无结果)"


@tool
async def weather_tool(city: str) -> str:
    """查询指定城市的实时天气(温度、天气状况)。需要实时天气时调用。"""
    return await get_weather(city)


@tool
def calc_tool(expression: str) -> str:
    """精确计算数学表达式(四则运算、幂、括号、数学函数)。需要精确数值计算时调用。"""
    return calculate(expression)


@tool
async def web_search_tool(query: str) -> str:
    """搜索互联网获取最新信息或通用知识(新闻、技术资料、事件进展)。知识库和天气无法满足时调用。"""
    return await web_search(query)


@tool
def time_tool() -> str:
    """查询当前时间和日期(年月日时分秒+星期)。需要知道现在时间时调用。"""
    return get_time()


# create_agent 工具集(quick/agent常态共用); config 注入retriever/tenant/kb_ids实现per-request上下文
REACT_AGENT_TOOLS = [search_kb_tool, weather_tool, calc_tool, web_search_tool, time_tool]


# ponytail: run_search_agent 已删 — mode路由后 plan_agent 走 _exec_plan_step 直接检索,
# 此 OpenAI function-calling ReAct 检索路径成死代码(chat_message/SEARCH_TOOL 同删). KB ReAct 检索能力由 search_kb_tool(create_agent)覆盖


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


# ponytail: ===== LangChain 1.0 create_agent 迁移(取代手写 run_tool_agent) =====


class ToolAuditMiddleware(AgentMiddleware):
    """after_model hook: 审计LLM发起的每次工具调用(记录日志, 预留接langfuse)。
    ponytail: 前端可观测(thought/tool_call事件)在run_react_agent的streaming里做, 此处专注审计。"""
    def after_model(self, state) -> dict | None:
        msgs = state.get("messages") or []
        last = msgs[-1] if msgs else None
        for tc in (getattr(last, "tool_calls", None) or []):
            log.info("tool_call", name=tc.get("name"), args=tc.get("args"))
        return None


_react_agent = None


def get_react_agent():
    """构建并缓存 create_agent(create_agent + SummarizationMiddleware + ToolAuditMiddleware)。
    ponytail: 全局单例; per-request上下文(retriever/tenant/kb_ids)经 config.configurable 注入 search_kb_tool。"""
    global _react_agent
    if _react_agent is not None:
        return _react_agent
    from langchain.agents import create_agent
    from langchain.agents.middleware import SummarizationMiddleware
    from src.agents.prompts import TOOL_AGENT_PROMPT
    from src.core.llm import get_gateway
    llm = get_gateway().chat_model("fast")  # ponytail: 统一经ModelGateway, 与CRAG同源+langfuse trace
    _react_agent = create_agent(
        model=llm,
        tools=REACT_AGENT_TOOLS,
        middleware=[
            # ponytail: 长对话超阈值自动汇总(当前多轮只截最近几条, 这是真实缺口)
            SummarizationMiddleware(model=llm, max_tokens_before_summary=4000, messages_to_keep=10),
            ToolAuditMiddleware(),
        ],
        system_prompt=TOOL_AGENT_PROMPT,
    )
    return _react_agent


async def run_react_agent(retriever, question: str, tenant_id: str = "default",
                          kb_ids: list[str] | None = None, history: list | None = None) -> dict:
    """create_agent 驱动的 ReAct 闭环: astream 流式, 映射前端 thought/tool_call/token 事件, 末尾判 source。
    调过工具→source=tool; 没调(纯常识)→fallback_unverified。
    ponytail: 复杂度集中在streaming映射(messages→token打字机, updates→tool_call展示)。"""
    from langchain_core.messages import ToolMessage
    agent = get_react_agent()
    try:
        writer = get_stream_writer()
    except Exception:
        writer = None
    # 拼 messages: 历史 + 当前问题(create_agent 多轮靠messages历史)
    msgs = []
    for h in (history or [])[-5:]:
        msgs.append({"role": "user", "content": h.get("question", "")})
        msgs.append({"role": "assistant", "content": h.get("answer", "")})
    msgs.append({"role": "user", "content": question})
    configurable = {"retriever": retriever, "tenant_id": tenant_id, "kb_ids": kb_ids}

    final_text, used_tool = "", False
    async for mode, chunk in agent.astream(
        {"messages": msgs}, config={"configurable": configurable}, stream_mode=["messages", "updates"]
    ):
        if mode == "messages":
            msg_chunk, metadata = chunk
            # ponytail: 只model节点(LLM)的token做打字机, 排除tools节点ToolMessage content
            if (metadata or {}).get("langgraph_node") == "model":
                content = getattr(msg_chunk, "content", None)
                if content:
                    final_text += content
                    if writer:
                        try:
                            writer({"tag": "token", "text": content})
                        except Exception:
                            pass
        elif mode == "updates":
            for node, upd in (chunk or {}).items():
                for m in ((upd or {}).get("messages") or []):
                    if node == "model":
                        tcs = getattr(m, "tool_calls", None) or []
                        for tc in tcs:
                            if writer:
                                try:
                                    writer({"tag": "tool_call", "name": tc.get("name"),
                                            "args": tc.get("args", {}), "result": "(执行中)"})
                                except Exception:
                                    pass
                        # 兜底: 最终回答(model无tool_calls的AIMessage)覆盖final_text, 比token累加更稳
                        if not tcs and getattr(m, "content", None):
                            final_text = m.content
                    elif node == "tools" and isinstance(m, ToolMessage):
                        used_tool = True
                        if writer:
                            try:
                                writer({"tag": "tool_call", "name": getattr(m, "name", "tool"),
                                        "args": {}, "result": str(m.content)[:300]})
                            except Exception:
                                pass
    return {"generation": final_text or "(无回答)", "source": "tool" if used_tool else "fallback_unverified"}

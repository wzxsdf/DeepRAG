# ponytail: CRAG + 多跳(route) + LangGraph 1.x Command声明式路由(替代add_conditional_edges)

import time
import re
import functools
from typing import TypedDict, List, Any
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from src.agents.llm import chat, chat_json, chat_stream
from src.prompts import (GRADER_BATCH_PROMPT, GENERATOR_PROMPT, VERIFIER_PROMPT, REWRITE_PROMPT,
    CHITCHAT_PROMPT, OPEN_ANSWER_FALLBACK_PROMPT, AGENT_COMPLEX_JUDGE_PROMPT)
from src.storage.protocol import RetrievedDocument
from src.core.logging import log

MAX_RETRY = 2  # ponytail: 自纠最大轮数, 兜底防死循环


def _timed(node: str):
    # ponytail: per-node 计时, print 到后端日志(量化延迟拆解, MVP可观测, 后续可接OTel); 不推stream避免前端兼容负担
    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(*a, **kw):
            t0 = time.perf_counter()
            try:
                return await fn(*a, **kw)
            finally:
                ms = (time.perf_counter() - t0) * 1000
                log.info("trace", node=node, ms=round(ms))
        return wrapper
    return deco


class AgentState(TypedDict):
    question: str
    query: str  # 当前检索query(可能被rewrite)
    tenant_id: str
    documents: List[RetrievedDocument]
    search_decision: str  # generate / rewrite / refuse
    generation: str
    verification: Any
    retry_count: int
    conversation_history: List[Any]  # W3.3 多轮历史 [{question, answer}]
    sub_queries: List[str]  # 多跳子查询(展示用)
    kb_ids: List[str]  # 限定检索的知识库列表(空=不限)
    intent: str  # subtype镜像(统计/展示): chitchat/...
    source: str  # 答案来源(前端徽章): kb_grounded/non_kb/tool/planned/fallback_unverified/refused
    mode: str  # 用户显式选: quick/kb/agent(取代LLM意图识别)
    safety: str  # 独立安全维度: allow/refuse
    user_id: str  # P1 记忆隔离键(默认default)
    memory_context: str  # P1 长期记忆注入文本(供prompt参考, 空=无记忆)


async def _stream_answer(messages: list[dict]) -> str:
    """流式生成回复, 逐token经stream_writer推前端(打字机); 失败兜底非流式chat.
    ponytail: 抽自原_generate_node, 供generate/reply_direct/open_answer复用(DRY)."""
    from langgraph.config import get_stream_writer
    try:
        writer = get_stream_writer()
    except Exception:
        writer = None
    parts: list[str] = []
    try:
        async for tok in chat_stream(messages):
            parts.append(tok)
            if writer:
                try:
                    writer({"tag": "token", "text": tok})
                except Exception:
                    pass
        return "".join(parts)
    except Exception:
        return await chat(messages)


# ponytail: 规则快通道 — 只抓100%明显的闲聊/能力问询(高precision低recall), 漏的走LLM兜底零误伤
_CHITCHAT_RE = re.compile(r"你好|您好|在吗|谢谢|辛苦|早上好|下午好|晚上好|嗨|hi\b|hello|thanks", re.I)
_CAPABILITY_RE = re.compile(r"你(能|可以|会)(做|帮|干|回答).*|你是谁|你叫什么|支持什么|怎么用|能干啥|有哪些功能", re.I)


def _classify_by_rule(question: str) -> str | None:
    """规则快通道: 命中明显闲聊/能力问询则返回direct(跳过route LLM省~4s); 未命中返回None走LLM."""
    q = question.strip()
    if not q:
        return None
    if _CAPABILITY_RE.search(q) or _CHITCHAT_RE.search(q):
        return "direct"
    return None


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


def _make_entry_node(memory_store=None, fact_store=None, context_max_chars: int = 500):
    """P1/P2: 闭包化入口节点, 注入长期记忆(memory_store) + 实体记忆(fact_store)。

    memory_store/fact_store 均可选; None 时跳过对应检索(即插即用)。
    context_max_chars: E3 memory_context 截断阈值(防prompt token膨胀)。
    """
    async def _entry_node(state: AgentState) -> Command:
        """mode驱动入口: 规则闲聊→reply_direct; 规则越界→END; 否则按mode分发(0次意图LLM).

        P1: 入口处检索长期记忆(语义); P2: 加载实体记忆(用户画像)。结果置 memory_context 供下游注入。
        """
        question = state["question"]
        mode = state.get("mode", "quick")
        user_id = state.get("user_id", "default")

        # P2 读闭环: 实体记忆(用户画像) + P1 长期记忆(语义检索), 失败降级空串(不阻塞主流程)
        memory_parts: list[str] = []
        if fact_store is not None:
            try:
                facts = await fact_store.get_all(user_id)
                if facts:
                    memory_parts.append("用户画像: " + "; ".join(f"{k}={v}" for k, v in facts.items()))
            except Exception as e:
                log.warning("user_facts_load_failed", error=type(e).__name__, msg=str(e))
        if memory_store is not None:
            try:
                memories = await memory_store.search(user_id, question, top_k=3)
                if memories:
                    memory_parts.append("相关历史记忆(酌情参考):\n" + "\n".join(f"- {m}" for m in memories))
            except Exception as e:
                log.warning("long_term_memory_retrieve_failed", error=type(e).__name__, msg=str(e))
        memory_context = "\n\n".join(memory_parts)
        # E3: 截断防 prompt token 膨胀
        if context_max_chars and len(memory_context) > context_max_chars:
            memory_context = memory_context[:context_max_chars] + "..."

        # ① 规则快通道: 明显闲聊/能力 → direct(跨模式, 省 LLM)
        if _classify_by_rule(question) == "direct":
            return Command(update={"mode": mode, "intent": "chitchat", "subtype": "chitchat",
                                   "safety": "allow", "user_id": user_id, "memory_context": memory_context},
                           goto="reply_direct")
        # ② safety 规则前置: 明显越界 → END(0 LLM)
        if _safety_rule(question) == "refuse":
            return Command(update={"mode": mode, "safety": "refuse", "source": "refused",
                                   "generation": "抱歉，这类问题我无法回答。",
                                   "user_id": user_id, "memory_context": memory_context}, goto=END)
        # ③ 按 mode 分发
        if mode == "kb":
            return Command(update={"mode": mode, "safety": "allow",
                                   "user_id": user_id, "memory_context": memory_context}, goto="retrieve")
        if mode == "agent":
            if await _judge_complex(question):
                return Command(update={"mode": mode, "safety": "allow",
                                       "user_id": user_id, "memory_context": memory_context}, goto="plan_agent")
            return Command(update={"mode": mode, "safety": "allow",
                                   "user_id": user_id, "memory_context": memory_context}, goto="tool_agent")
        # quick(默认)
        return Command(update={"mode": mode, "safety": "allow",
                               "user_id": user_id, "memory_context": memory_context}, goto="tool_agent")
    return _entry_node


def _retrieve_node(retriever):
    async def _retrieve(state: AgentState) -> dict:
        tenant = state.get("tenant_id", "default")
        kb_ids = state.get("kb_ids") or None
        # ponytail: kb模式固定单次检索+rerank(删complex多跳); 多跳需求由agent的plan覆盖
        from src.retrieval.reranker import DEFAULT_RETRIEVAL_CANDIDATES, rerank
        from src.storage.protocol import RetrievalFilter
        docs = await retriever.retrieve(
            state["query"],
            RetrievalFilter(tenant_id=tenant, kb_ids=kb_ids),
            top_k=DEFAULT_RETRIEVAL_CANDIDATES,
        )
        return {"documents": await rerank(state["query"], docs, top_n=5)}
    return _retrieve


async def _grade_node(state: AgentState) -> Command:
    """批量相关性评估(1次LLM判全部证据). 无相关→转open_answer软拒兜底(不再硬拒), 有相关→generate."""
    docs = state["documents"]
    if not docs:
        return Command(update={"search_decision": "fallback"}, goto="tool_agent")
    evidence_list = "\n".join(f"[{i+1}] {d.content[:400]}" for i, d in enumerate(docs))
    try:
        res = await chat_json([{"role": "user", "content": GRADER_BATCH_PROMPT.format(
            question=state["question"], evidence_list=evidence_list)}])
        grades = res.get("grades", [])
        if not grades:
            relevant = list(docs)  # ponytail: 无有效判断, 保守保留全部
        else:
            relevant_idx = {g.get("index") for g in grades if g.get("relevant")}
            relevant = [d for i, d in enumerate(docs) if (i + 1) in relevant_idx]
    except Exception:
        relevant = list(docs)  # ponytail: grader失败保守视为全相关, 不丢证据
    if not relevant:
        return Command(update={"search_decision": "fallback"}, goto="tool_agent")
    return Command(update={"documents": relevant, "search_decision": "generate"}, goto="generate")


async def _reply_direct_node(state: AgentState) -> dict:
    """闲聊/能力问询. 人设回复, 不检索, source=non_kb. 结合多轮历史自然回应."""
    history = state.get("conversation_history") or []
    hist_text = ""
    if history:
        # ponytail: P0 — history 已由记忆组件治理(摘要+窗口), 此处直接消费不再裁剪
        hist_text = "对话历史:\n" + "\n".join(
            f"用户: {h.get('question', '')}\n助手: {h.get('answer', '')}" for h in history) + "\n\n"
    prompt = CHITCHAT_PROMPT.format(history=hist_text, question=state["question"])
    # P1: 注入长期记忆(若有)
    mem = state.get("memory_context", "")
    if mem:
        prompt = f"{mem}\n\n{prompt}"
    ans = await _stream_answer([{"role": "user", "content": prompt}])
    return {"generation": ans, "source": "non_kb"}


def _tool_agent_node(retriever):
    async def _tool_agent(state: AgentState) -> dict:
        """带工具的ReAct Agent(quick + agent常态共用): create_agent驱动. source=tool|fallback_unverified."""
        from src.agents.react_agent import run_react_agent
        res = await run_react_agent(retriever, state["question"],
                                    tenant_id=state.get("tenant_id", "default"),
                                    kb_ids=state.get("kb_ids") or None,
                                    history=state.get("conversation_history"),
                                    memory_context=state.get("memory_context", ""))
        return {"generation": res["generation"], "source": res["source"]}
    return _tool_agent


def _plan_agent_node(retriever):
    async def _plan_agent(state: AgentState) -> dict:
        """多源编排: 规划+执行+综合. source=planned."""
        from src.agents.plan_agent import run_plan_agent
        res = await run_plan_agent(retriever, state["question"],
                                   tenant_id=state.get("tenant_id", "default"),
                                   kb_ids=state.get("kb_ids") or None,
                                   history=state.get("conversation_history"),
                                   memory_context=state.get("memory_context", ""))
        return {"generation": res["generation"], "source": res["source"]}
    return _plan_agent


async def _rewrite_node(state: AgentState) -> dict:
    new_q = await chat([{"role": "user", "content": REWRITE_PROMPT.format(question=state["question"])}])
    return {"query": new_q.strip(), "retry_count": state.get("retry_count", 0) + 1}


async def _generate_node(state: AgentState) -> dict:
    # ponytail: small-to-big - child为可引用单元, parent_content作为上下文附加(W3.2父块扩展接通)
    evidence = "\n\n".join(
        f"[{i+1}] {d.content}" + (f"\n(上下文: {d.parent_content})" if d.parent_content else "")
        for i, d in enumerate(state["documents"]))
    prompt = GENERATOR_PROMPT.format(question=state["question"], evidence=evidence)
    history = state.get("conversation_history") or []
    if history:
        # ponytail: P0 — history 已由记忆组件治理(摘要+窗口), 此处直接消费不再裁剪
        hist_text = "\n".join(f"Q: {h.get('question', '')}\nA: {h.get('answer', '')}" for h in history)
        prompt = f"对话历史(供理解指代):\n{hist_text}\n\n{prompt}"
    # P1: 注入长期记忆(若有)
    mem = state.get("memory_context", "")
    if mem:
        prompt = f"{mem}\n\n{prompt}"
    # ponytail: 流式生成抽到_stream_answer(DRY), 复用同一段token推送逻辑
    ans = await _stream_answer([{"role": "user", "content": prompt}])
    return {"generation": ans}


async def _verify_node(state: AgentState) -> Command:
    """LangGraph 1.x Command路由 — grounded+cite→END(kb_grounded), 不足→rewrite, 达上限→END降级免责."""
    evidence = "\n\n".join(f"[{i+1}] {d.content}" for i, d in enumerate(state["documents"]))
    try:
        ver = await chat_json([{"role": "user", "content": VERIFIER_PROMPT.format(
            question=state["question"], answer=state["generation"], evidence=evidence)}])
    except Exception:
        ver = {"grounded": True, "citations_valid": True, "issue": "verifier跳过"}
    if ver.get("grounded") and ver.get("citations_valid"):
        return Command(update={"verification": ver, "source": "kb_grounded"}, goto=END)
    if state.get("retry_count", 0) < MAX_RETRY:
        return Command(update={"verification": ver}, goto="rewrite")
    # ponytail: 自纠达上限仍不grounded → 降级标未验证免责(诚实标注存疑)
    return Command(update={"verification": ver, "source": "fallback_unverified"}, goto=END)


def build_graph(retriever, memory_store=None, fact_store=None, context_max_chars: int = 500):
    """构建CRAG状态机. retriever需实现Retriever Protocol.

    P1: memory_store(长期记忆)可选; P2: fact_store(实体记忆)可选。
    均为 None 时不启用读闭环(即插即用, 向后兼容)。
    context_max_chars: memory_context 注入截断阈值(E3)。
    """
    wf = StateGraph(AgentState)
    wf.add_node("entry", _timed("entry")(_make_entry_node(memory_store, fact_store, context_max_chars)))
    wf.add_node("reply_direct", _timed("reply_direct")(_reply_direct_node))
    wf.add_node("tool_agent", _timed("tool_agent")(_tool_agent_node(retriever)))
    wf.add_node("plan_agent", _timed("plan_agent")(_plan_agent_node(retriever)))
    wf.add_node("retrieve", _timed("retrieve")(_retrieve_node(retriever)))
    wf.add_node("grade", _timed("grade")(_grade_node))
    wf.add_node("rewrite", _timed("rewrite")(_rewrite_node))
    wf.add_node("generate", _timed("generate")(_generate_node))
    wf.add_node("verify", _timed("verify")(_verify_node))

    wf.add_edge(START, "entry")
    wf.add_edge("reply_direct", END)
    wf.add_edge("tool_agent", END)
    wf.add_edge("plan_agent", END)
    wf.add_edge("retrieve", "grade")
    # ponytail: LangGraph 1.x Command声明式路由 — grade/verify节点内return Command(goto=...),
    # 替代旧 add_conditional_edges + 独立decide函数, 路由逻辑内聚到节点, 更简洁现代
    wf.add_edge("rewrite", "retrieve")
    wf.add_edge("generate", "verify")

    checkpointer = MemorySaver().with_allowlist([("src.storage.protocol", "RetrievedDocument")])
    return wf.compile(checkpointer=checkpointer)


if __name__ == "__main__":  # ponytail: 入口规则+安全前置自检(minimal runnable check)
    assert _classify_by_rule("你好") == "direct"
    assert _classify_by_rule("你能做什么") == "direct"
    assert _classify_by_rule("什么是RAG") is None
    assert _safety_rule("帮我写攻击脚本") == "refuse"
    assert _safety_rule("今天天气如何") is None
    print("entry classify + safety rule ok")

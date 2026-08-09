# Plan Agent - 多源编排规划Agent

from langgraph.config import get_stream_writer


async def run_plan_agent(retriever, question: str, tenant_id: str = "default",
                         kb_ids: list[str] | None = None, history: list | None = None,
                         memory_context: str = "") -> dict:
    """多源编排Agent: LLM显式规划步骤→逐步执行(KB检索/工具)→流式综合. source=planned.

    memory_context: P1/P2 长期+实体记忆, 注入综合阶段(规划阶段不注入, 记忆主要影响答案而非步骤分解)。
    ponytail: 规划失败→退化单步kb; 综合失败→提示. 规划用chat_json(非function-calling), 一次性产出可控可观测.
    """
    from src.tools import get_weather, calculate, web_search
    from src.storage.protocol import RetrievalFilter
    from src.retrieval.reranker import rerank
    from src.prompts import PLAN_PROMPT
    from src.agents.llm import chat

    MAX_PLAN_STEPS = 4  # ponytail: 多源组合通常2-4步

    async def _exec_plan_step(step: dict) -> tuple:
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

    try:
        writer = get_stream_writer()
    except Exception:
        writer = None

    # ① 规划
    try:
        from src.agents.llm import chat_json
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
        typ, arg, result = await _exec_plan_step(s)
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
    # P1/P2: 长期记忆(用户画像+历史)前置注入综合阶段, 影响最终答案
    if memory_context:
        syn_prompt = f"{memory_context}\n\n{syn_prompt}"
    try:
        from src.agents.graph import _stream_answer
        ans = await _stream_answer([{"role": "user", "content": syn_prompt}])
    except Exception:
        ans = await chat([{"role": "user", "content": syn_prompt}])

    return {"generation": ans, "source": "planned"}

# ReAct Agent - 工具调用闭环Agent

from langgraph.config import get_stream_writer
from langchain_core.messages import ToolMessage


async def run_react_agent(retriever, question: str, tenant_id: str = "default",
                          kb_ids: list[str] | None = None, history: list | None = None,
                          memory_context: str = "") -> dict:
    """create_agent 驱动的 ReAct 闭环: astream 流式, 映射前端 thought/tool_call/token 事件。

    调过工具→source=tool; 没调(纯常识)→fallback_unverified.
    memory_context: P1/P2 长期+实体记忆(用户画像/历史), 拼到当前问题前作为背景。

    ponytail: 复杂度集中在streaming映射(messages→token打字机, updates→tool_call展示)。
    """
    from src.agents.builders import get_react_agent

    agent = get_react_agent()
    try:
        writer = get_stream_writer()
    except Exception:
        writer = None

    # ponytail: P0 — history 已由记忆组件治理(摘要+窗口), 此处直接消费不再裁剪
    msgs = []
    for h in (history or []):
        msgs.append({"role": "user", "content": h.get("question", "")})
        msgs.append({"role": "assistant", "content": h.get("answer", "")})
    # P1/P2: 长期记忆(用户画像+历史)作为背景拼到当前问题前, 不混入system_prompt(便携, 不依赖system合并)
    q = f"{memory_context}\n\n当前问题: {question}" if memory_context else question
    msgs.append({"role": "user", "content": q})
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

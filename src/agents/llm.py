# ponytail: LLM封装 — 经ModelGateway(init_chat_model→BaseChatModel), httpx直连退役, langfuse自动trace
# 保持 chat/chat_json/chat_stream 函数签名不变(graph.py/tools.py零改); 死代码chat_message随run_search_agent已删
# temperature参数保留兼容签名但gateway固定0(所有调用方均用默认0); purpose: chat_stream→generate, chat/json→fast

import json
import re

from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

from src.core.llm import get_gateway

_ROLE_MSG = {"system": SystemMessage, "user": HumanMessage, "assistant": AIMessage}


def _to_lc(messages: list[dict]) -> list:
    """OpenAI风格 dict messages → LangChain Message. 支持system/user/assistant(调用方只用这三类)."""
    return [_ROLE_MSG.get(m["role"], HumanMessage)(content=m["content"]) for m in messages]


async def chat_stream(messages: list[dict], temperature: float = 0.0):
    """流式chat, yield文本delta. 供生成节点逐字推送(SSE token事件). 走generate purpose(强模型)."""
    m = get_gateway().chat_model("generate")
    async for chunk in m.astream(_to_lc(messages)):
        if chunk.content:
            yield chunk.content


async def chat(messages: list[dict], temperature: float = 0.0) -> str:
    """OpenAI兼容chat, 返回content文本. 走fast purpose(rewrite/兜底, 轻量)."""
    r = await get_gateway().chat_model("fast").ainvoke(_to_lc(messages))
    return r.content


async def chat_json(messages: list[dict], temperature: float = 0.0) -> dict:
    """要求JSON输出. 先试response_format(json_object), 不支持/失败则回退普通chat+正则解析. 走fast purpose."""
    gw = get_gateway()
    try:
        mj = gw.chat_model("fast").bind(response_format={"type": "json_object"})
        return _parse_json((await mj.ainvoke(_to_lc(messages))).content)
    except Exception:
        return _parse_json((await gw.chat_model("fast").ainvoke(_to_lc(messages))).content)


def _parse_json(content: str) -> dict:
    """从LLM输出解析JSON(兼容```json代码块包裹)."""
    content = content.strip()
    if content.startswith("```"):
        m = re.search(r"```(?:json)?\s*(.+?)```", content, re.S)
        if m:
            content = m.group(1).strip()
    return json.loads(content)


if __name__ == "__main__":  # ponytail: 自检 — chat/chat_json/chat_stream 三路径走gateway
    import asyncio

    async def t():
        print("chat:", (await chat([{"role": "user", "content": "只回复:好"}]))[:20])
        print("chat_json:", await chat_json([{"role": "user", "content": '返回JSON {"ok":true}'}]))
        toks = [x async for x in chat_stream([{"role": "user", "content": "数1到3"}])]
        print("stream:", "".join(toks)[:20])

    asyncio.run(t())

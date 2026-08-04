# ponytail: LLM封装, httpx直连juaiapi(OpenAI兼容), 不引入langchain

import os
import json
import re
import asyncio
import httpx
from dotenv import load_dotenv

load_dotenv()

_CHAT_URL = os.getenv("LITELLM_BASE_URL", "") + "/chat/completions"
_CHAT_HEADERS = {"Authorization": f"Bearer {os.getenv('LITELLM_API_KEY', '')}", "Content-Type": "application/json"}
_MODEL = os.getenv("LITELLM_MODEL", "gpt-4o-mini")


async def _post(payload: dict) -> dict:
    """POST chat completions 带重试. 5xx(503等瞬时服务端错误)重试, 4xx(参数/不支持)不重试直接抛."""
    last = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=90) as c:
                r = await c.post(_CHAT_URL, headers=_CHAT_HEADERS, json=payload)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPStatusError as e:
            if 500 <= e.response.status_code < 600 and attempt < 2:
                last = e
                await asyncio.sleep(1.5 * (attempt + 1))  # ponytail: 5xx瞬时, 重试
                continue
            raise  # 4xx或不支持的功能(response_format/tools)直接抛, 上层fallback
        except Exception as e:
            last = e
            if attempt < 2:
                await asyncio.sleep(1.5 * (attempt + 1))
    raise last


async def _call(messages: list[dict], temperature: float = 0.0, json_mode: bool = False) -> str:
    payload = {"model": _MODEL, "messages": messages, "temperature": temperature}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    return (await _post(payload))["choices"][0]["message"]["content"]


async def chat_message(messages: list[dict], tools: list[dict] | None = None,
                       temperature: float = 0.0) -> dict:
    """调用LLM返回原始assistant message(含content或tool_calls). 供Agent工具调用循环使用."""
    payload = {"model": _MODEL, "messages": messages, "temperature": temperature}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    return (await _post(payload))["choices"][0]["message"]


async def chat_stream(messages: list[dict], temperature: float = 0.0):
    """流式chat, yield文本delta. 供生成节点逐字推送(SSE token事件). 无重试, 失败由调用方兜底."""
    payload = {"model": _MODEL, "messages": messages, "temperature": temperature, "stream": True}
    async with httpx.AsyncClient(timeout=120) as c:
        async with c.stream("POST", _CHAT_URL, headers=_CHAT_HEADERS, json=payload) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    delta = json.loads(data)["choices"][0]["delta"].get("content")
                except Exception:
                    continue
                if delta:
                    yield delta


async def chat(messages: list[dict], temperature: float = 0.0) -> str:
    """OpenAI兼容chat, 返回content文本."""
    return await _call(messages, temperature, json_mode=False)


async def chat_json(messages: list[dict], temperature: float = 0.0) -> dict:
    """要求JSON输出. 先试response_format, 不支持则回退普通chat+正则解析."""
    try:
        return _parse_json(await _call(messages, temperature, json_mode=True))
    except Exception:
        return _parse_json(await _call(messages, temperature, json_mode=False))


def _parse_json(content: str) -> dict:
    """从LLM输出解析JSON(兼容```json代码块包裹)."""
    content = content.strip()
    if content.startswith("```"):
        m = re.search(r"```(?:json)?\s*(.+?)```", content, re.S)
        if m:
            content = m.group(1).strip()
    return json.loads(content)

# ponytail: LLM封装 - 优化版，使用最新的LangChain 1.x结构化输出API

import json
import re
from typing import Any, Optional
from pydantic import create_model

from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

from src.core.llm.gateway import get_gateway

_ROLE_MSG = {"system": SystemMessage, "user": HumanMessage, "assistant": AIMessage}


def _to_lc(messages: list[dict]) -> list:
    """OpenAI风格 dict messages → LangChain Message.

    支持system/user/assistant(调用方只用这三类)。
    自动处理role映射和消息转换。

    Args:
        messages: OpenAI风格的消息列表，每个消息包含role和content

    Returns:
        list: LangChain Message对象列表

    Examples:
        >>> _to_lc([{"role": "user", "content": "你好"}])
        [HumanMessage(content='你好')]
    """
    return [_ROLE_MSG.get(m["role"], HumanMessage)(content=m["content"]) for m in messages]


async def chat_stream(messages: list[dict], temperature: float = 0.0):
    """流式chat, yield文本delta。

    供生成节点逐字推送(SSE token事件)。
    走generate purpose(强模型)，支持实时输出。

    Args:
        messages: 对话消息列表
        temperature: 温度参数(当前固定0)

    Yields:
        str: 每次生成的文本片段

    Examples:
        >>> async for token in chat_stream([{"role": "user", "content": "讲个故事"}]):
        ...     print(token, end='')
    """
    m = get_gateway().chat_model("generate")
    async for chunk in m.astream(_to_lc(messages)):
        if chunk.content:
            yield chunk.content


async def chat(messages: list[dict], temperature: float = 0.0) -> str:
    """OpenAI兼容chat, 返回content文本。

    走fast purpose(rewrite/兜底, 轻量)。
    简单对话场景的最优选择。

    Args:
        messages: 对话消息列表
        temperature: 温度参数(当前固定0)

    Returns:
        str: LLM响应的文本内容

    Examples:
        >>> result = await chat([{"role": "user", "content": "你好"}])
        >>> print(result)
    """
    r = await get_gateway().chat_model("fast").ainvoke(_to_lc(messages))
    return r.content


async def chat_json(
    messages: list[dict],
    temperature: float = 0.0,
    schema: Optional[dict] = None
) -> dict:
    """要求JSON输出，使用最新的with_structured_output API。

    优先使用结构化输出(with_structured_output)，
    不支持时回退到response_format + 正则解析。
    走fast purpose(判断、评估等轻量任务)。

    Args:
        messages: 对话消息列表
        temperature: 温度参数(当前固定0)
        schema: 可选的JSON Schema定义，用于强制输出格式

    Returns:
        dict: 解析后的JSON对象

    Examples:
        >>> result = await chat_json([{"role": "user", "content": "返回JSON"}])
        >>> print(result)  # {"key": "value"}

        >>> schema = {"type": "object", "properties": {"name": {"type": "string"}}}
        >>> result = await chat_json([{"role": "user", "content": "你的名字"}], schema=schema)
    """
    gw = get_gateway()

    # 方案1: 尝试使用with_structured_output (最佳方案)
    if schema:
        try:
            # 动态创建Pydantic模型
            model_class = _create_pydantic_model(schema)
            structured_model = gw.chat_model("fast").with_structured_output(model_class)
            result = await structured_model.ainvoke(_to_lc(messages))
            return result.model_dump() if hasattr(result, 'model_dump') else result
        except Exception:
            pass  # 回退到方案2

    # 方案2: 尝试使用bind(response_format) (兼容方案)
    try:
        mj = gw.chat_model("fast").bind(response_format={"type": "json_object"})
        content = (await mj.ainvoke(_to_lc(messages))).content
        return _parse_json(content)
    except Exception:
        pass  # 回退到方案3

    # 方案3: 普通调用 + 正则解析 (兜底方案)
    try:
        content = (await gw.chat_model("fast").ainvoke(_to_lc(messages))).content
        return _parse_json(content)
    except Exception as e:
        # 所有方案失败，返回错误结构
        return {
            "error": f"JSON解析失败: {type(e).__name__}",
            "raw_content": content[:200] if 'content' in locals() else ""
        }


def _create_pydantic_model(schema: dict) -> type:
    """根据JSON Schema动态创建Pydantic模型。

    Args:
        schema: JSON Schema定义

    Returns:
        type: Pydantic模型类
    """
    if "properties" not in schema:
        # 简单类型，创建通用模型
        return create_model(
            "DynamicModel",
            __config__=type('Config', (), {'extra': 'allow'}),
            value=(Any, ...)
        )

    fields = {}
    required = schema.get("required", [])
    properties = schema.get("properties", {})

    for field_name, field_def in properties.items():
        field_type = _get_python_type(field_def)
        default = ... if field_name in required else None
        fields[field_name] = (field_type, default)

    return create_model(
        "DynamicModel",
        __config__=type('Config', (), {'extra': 'allow'}),
        **fields
    )


def _get_python_type(field_def: dict) -> type:
    """将JSON Schema类型映射到Python类型。

    Args:
        field_def: 字段定义

    Returns:
        type: 对应的Python类型
    """
    field_type = field_def.get("type", "string")

    type_mapping = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "array": list,
        "object": dict
    }

    return type_mapping.get(field_type, str)


def _parse_json(content: str) -> dict:
    """从LLM输出解析JSON(兼容```json代码块包裹)。

    Args:
        content: LLM输出的文本内容

    Returns:
        dict: 解析后的JSON对象

    Examples:
        >>> _parse_json('{"key": "value"}')
        {'key': 'value'}
        >>> _parse_json('```json\\n{"key": "value"}\\n```')
        {'key': 'value'}
    """
    content = content.strip()

    # 移除代码块标记
    if content.startswith("```"):
        match = re.search(r"```(?:json)?\s*(.+?)```", content, re.S)
        if match:
            content = match.group(1).strip()

    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        # 尝试修复常见错误
        content = _fix_common_json_errors(content)
        try:
            return json.loads(content)
        except:
            return {
                "error": f"JSON解析失败: {str(e)}",
                "raw_content": content[:200]
            }


def _fix_common_json_errors(content: str) -> str:
    """尝试修复常见的JSON格式错误。

    Args:
        content: 可能有错误的JSON字符串

    Returns:
        str: 修复后的JSON字符串
    """
    # 移除尾部逗号
    content = re.sub(r',\s*}', '}', content)
    content = re.sub(r',\s*]', ']', content)

    # 修复单引号为双引号
    content = re.sub(r"'([^']*)'", r'"\1"', content)

    return content


if __name__ == "__main__":
    import asyncio

    async def test():
        print("=== LLM优化测试 ===")

        # 测试基本对话
        result = await chat([{"role": "user", "content": "只回复:好"}])
        print(f"[OK] chat: {result[:20]}")

        # 测试JSON输出
        json_result = await chat_json([{"role": "user", "content": '返回JSON {"ok":true}'}])
        print(f"[OK] chat_json: {json_result}")

        # 测试流式输出
        tokens = [x async for x in chat_stream([{"role": "user", "content": "数1到3"}])]
        print(f"[OK] chat_stream: {''.join(tokens)[:20]}")

        print("✅ 所有测试通过")

    asyncio.run(test())

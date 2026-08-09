# 搜索工具 - 优化版

import httpx
import os
from typing import Optional, List
from pydantic import BaseModel, Field

from langchain_core.tools import tool


# ponytail: 搜索结果结构化输出
class SearchResult(BaseModel):
    """搜索结果结构化输出"""
    answer: str = Field(default="", description="AI生成的答案摘要")
    results: List[str] = Field(default_factory=list, description="搜索结果列表")
    success: bool = Field(description="是否成功")
    error: Optional[str] = Field(default=None, description="错误信息")


async def web_search(query: str) -> str:
    """使用Tavily API进行网络搜索，返回答案摘要和搜索结果。

    需要设置环境变量 TAVILY_API_KEY。
    支持实时网络信息搜索，包括新闻、技术资料、事件进展等。

    Args:
        query: 搜索查询，如 "最新AI发展" 或 "Python 3.12新特性"

    Returns:
        str: 搜索结果，包含AI生成的答案摘要和top3搜索结果，或错误信息

    Examples:
        >>> await web_search("2024年AI趋势")
        "根据搜索结果，2024年AI的主要趋势包括...\n\n[1] 多模态AI: ...\n[2] ..."
        >>> await web_search("Python性能优化")
        "[1] Python性能优化技巧: ...\n[2] 使用asyncio提高并发性能: ..."
    """
    api_key = os.getenv("TAVILY_API_KEY", "")
    if not api_key:
        return "(未配置搜索 key, 无法联网搜索)"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": api_key,
                    "query": query,
                    "max_results": 3,
                    "search_depth": "basic"
                }
            )
            response.raise_for_status()
            data = response.json()

        answer = (data.get("answer") or "").strip()
        results = data.get("results", [])[:3]

        parts = []
        if answer:
            parts.append(answer)

        for i, item in enumerate(results, 1):
            title = item.get('title', '')
            content = item.get('content', '')[:200]
            parts.append(f"[{i}] {title}: {content}")

        return "\n".join(parts) or "(搜索无结果)"

    except httpx.TimeoutException:
        return "(搜索服务请求超时)"
    except httpx.HTTPStatusError as e:
        return f"(搜索服务错误: {e.response.status_code})"
    except Exception:
        return "(搜索服务暂不可用)"


@tool
async def web_search_tool(query: str) -> str:
    """搜索互联网获取最新信息或通用知识。

    当知识库和天气工具无法满足需求时使用此工具。
    适用于获取新闻、技术资料、事件进展等实时信息。

    Args:
        query: 搜索查询，可以是任何问题或主题

    Returns:
        str: 搜索结果，包含AI生成的答案摘要和相关链接，或错误信息

    Examples:
        >>> await web_search_tool.invoke("最新AI芯片发展")
        "根据搜索结果，最新的AI芯片发展包括...\n\n[1] 英伟达发布H100: ..."
        >>> await web_search_tool.invoke("Rust语言特点")
        "[1] Rust语言主要特点: 内存安全、高性能..."
    """
    return await web_search(query)


__all__ = ["web_search", "web_search_tool", "SearchResult"]

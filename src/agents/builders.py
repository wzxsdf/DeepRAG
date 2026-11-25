# ponytail: Agent构建器 - 优化版，使用最新的LangChain 1.x API

from src.tools import REACT_AGENT_TOOLS
from src.agents.middleware import ToolAuditMiddleware
from src.prompts import TOOL_AGENT_PROMPT
from src.core.llm import get_gateway


# 全局Agent缓存
_react_agent = None


def get_react_agent(
    max_tokens_before_summary: int = 4000,
    messages_to_keep: int = 10,
    enable_streaming: bool = True
):
    """构建并缓存 create_agent，使用最新的LangChain 1.x API。

    创建一个支持工具调用的ReAct Agent，具备以下特性：
    - 自动工具选择和调用
    - 长对话自动摘要
    - 工具调用审计
    - 流式输出支持

    Args:
        max_tokens_before_summary: 触发对话摘要的token阈值，默认4000
        messages_to_keep: 摘要后保留的最近消息数量，默认10条
        enable_streaming: 是否启用流式输出，默认True

    Returns:
        CompiledLangGraph: 编译后的Agent实例，可直接调用或流式处理

    Examples:
        >>> agent = get_react_agent()
        >>> result = agent.invoke({"messages": [{"role": "user", "content": "计算2+3"}]})

        >>> agent = get_react_agent(max_tokens_before_summary=6000)
        >>> async for chunk in agent.stream({"messages": [...]}):
        ...     print(chunk)
    """
    global _react_agent
    if _react_agent is not None:
        return _react_agent

    from langchain.agents import create_agent
    from langchain.agents.middleware import SummarizationMiddleware

    llm = get_gateway().chat_model("fast")

    # 使用最新的create_agent API
    _react_agent = create_agent(
        model=llm,
        tools=REACT_AGENT_TOOLS,
        middleware=[
            # 长对话自动摘要中间件
            SummarizationMiddleware(
                model=llm,
                max_tokens_before_summary=max_tokens_before_summary,
                messages_to_keep=messages_to_keep
            ),
            # 工具调用审计中间件
            ToolAuditMiddleware(),
        ],
        system_prompt=TOOL_AGENT_PROMPT,
    )

    return _react_agent


def clear_agent_cache():
    """清除Agent缓存，强制重新构建。

    在配置更改或需要刷新Agent时调用。
    下次调用get_react_agent()将重新构建Agent实例。

    Examples:
        >>> clear_agent_cache()
        >>> agent = get_react_agent()  # 重新构建
    """
    global _react_agent
    _react_agent = None


__all__ = ["get_react_agent", "clear_agent_cache"]

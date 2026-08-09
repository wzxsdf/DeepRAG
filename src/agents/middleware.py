# ponytail: Agent中间件层 - 优化版，增强审计和可观测性

from typing import Any, Dict, Optional
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import BaseMessage
from src.core.logging import log


class ToolAuditMiddleware(AgentMiddleware):
    """工具调用审计中间件 - after_model hook增强版。

    功能：
    - 记录每次工具调用的详细信息
    - 支持结构化日志输出
    - 预留 langfuse 接入点
    - 提供工具调用统计信息

    Examples:
        >>> middleware = ToolAuditMiddleware()
        >>> agent = create_agent(..., middleware=[middleware])
    """

    def __init__(self, enable_detailed_logging: bool = True):
        """初始化工具审计中间件。

        Args:
            enable_detailed_logging: 是否启用详细日志记录
        """
        super().__init__()
        self.enable_detailed_logging = enable_detailed_logging
        self._tool_call_count = {}  # 工具调用统计

    def after_model(self, state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """after_model hook: 审计LLM发起的每次工具调用。

        记录工具名称、参数、调用时间等信息。
        支持结构化日志输出和langfuse集成。

        Args:
            state: 当前Agent状态，包含messages等信息

        Returns:
            Optional[Dict]: 修改后的状态(通常返回None不修改状态)
        """
        messages = state.get("messages") or []
        if not messages:
            return None

        last_message = messages[-1] if messages else None
        if not last_message:
            return None

        # 检查是否有工具调用
        tool_calls = getattr(last_message, "tool_calls", None) or []
        if not tool_calls:
            return None

        # 记录每次工具调用
        for tool_call in tool_calls:
            self._audit_tool_call(tool_call)

        return None

    def _audit_tool_call(self, tool_call: Dict[str, Any]) -> None:
        """审计单次工具调用。

        Args:
            tool_call: 工具调用信息，包含name、args等字段
        """
        tool_name = tool_call.get("name", "unknown")
        tool_args = tool_call.get("args", {})

        # 更新调用统计
        self._tool_call_count[tool_name] = self._tool_call_count.get(tool_name, 0) + 1

        # 结构化日志记录
        log.info(
            "tool_call",
            tool=tool_name,
            args_count=len(tool_args),
            call_count=self._tool_call_count[tool_name],
            args=self._sanitize_args(tool_args) if self.enable_detailed_logging else {}
        )

        # 预留：langfuse事件记录
        # if langfuse_enabled:
        #     langfuse.log_tool_call(tool_name, tool_args)

    def _sanitize_args(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """清理敏感参数，避免记录敏感信息。

        Args:
            args: 原始参数字典

        Returns:
            Dict: 清理后的参数字典
        """
        sensitive_keys = {"password", "token", "api_key", "secret"}
        sanitized = {}

        for key, value in args.items():
            if key.lower() in sensitive_keys:
                sanitized[key] = "***REDACTED***"
            elif isinstance(value, str) and len(value) > 100:
                sanitized[key] = f"{value[:100]}..."
            else:
                sanitized[key] = value

        return sanitized

    def get_tool_stats(self) -> Dict[str, int]:
        """获取工具调用统计信息。

        Returns:
            Dict: 工具名称到调用次数的映射
        """
        return self._tool_call_count.copy()

    def reset_stats(self) -> None:
        """重置工具调用统计。"""
        self._tool_call_count.clear()


class StreamingLogMiddleware(AgentMiddleware):
    """流式日志中间件 - 实时记录Agent执行过程。

    功能：
    - 实时记录Agent的思考和执行过程
    - 支持前端可观测性展示
    - 提供结构化的事件流

    Examples:
        >>> middleware = StreamingLogMiddleware()
        >>> agent = create_agent(..., middleware=[middleware])
    """

    def __init__(self, enable_thought_logging: bool = True):
        """初始化流式日志中间件。

        Args:
            enable_thought_logging: 是否记录思考过程
        """
        super().__init__()
        self.enable_thought_logging = enable_thought_logging
        self._events = []  # 事件历史

    def after_model(self, state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """after_model hook: 记录模型输出和思考过程。

        Args:
            state: 当前Agent状态

        Returns:
            Optional[Dict]: 不修改状态
        """
        messages = state.get("messages") or []
        if not messages:
            return None

        last_message = messages[-1] if messages else None
        if not last_message:
            return None

        content = getattr(last_message, "content", None)
        if content and self.enable_thought_logging:
            self._log_event("thought", content=content[:200])

        return None

    def _log_event(self, event_type: str, **kwargs) -> None:
        """记录事件。

        Args:
            event_type: 事件类型
            **kwargs: 事件相关数据
        """
        event = {"type": event_type, **kwargs}
        self._events.append(event)
        log.debug("agent_event", **event)

    def get_event_history(self) -> list:
        """获取事件历史记录。

        Returns:
            list: 事件列表
        """
        return self._events.copy()

    def clear_events(self) -> None:
        """清除事件历史。"""
        self._events.clear()


__all__ = ["ToolAuditMiddleware", "StreamingLogMiddleware"]

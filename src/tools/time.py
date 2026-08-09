# 时间工具 - 优化版

from datetime import datetime, timezone, timedelta
from typing import Optional
from pydantic import BaseModel, Field

from langchain_core.tools import tool


# ponytail: 时间查询结果结构化输出
class TimeResult(BaseModel):
    """时间查询结果结构化输出"""
    datetime: str = Field(description="完整日期时间")
    date: str = Field(description="日期部分")
    time: str = Field(description="时间部分")
    weekday: str = Field(description="星期几")
    timezone: str = Field(description="时区")


def get_time() -> str:
    """获取当前东八区(中国标准时间)时间字符串。

    使用标准库datetime，无外部API依赖，固定东八区不引入zoneinfo依赖。
    返回格式化的日期时间字符串，包含年月日时分秒和星期。

    Returns:
        str: 格式化的时间字符串，格式 "YYYY-MM-DD HH:MM:SS 星期X"

    Examples:
        >>> get_time()
        "2024-08-07 14:30:25 星期三"
        >>> get_time()
        "2024-08-07 22:45:10 星期三"
    """
    tz = timezone(timedelta(hours=8))  # 东八区
    now = datetime.now(tz)

    # 星期映射
    weekdays = "周一周二周三周四周五周六周日"
    weekday = weekdays[now.weekday() * 2: now.weekday() * 2 + 2]

    return now.strftime(f"%Y-%m-%d %H:%M:%S {weekday}")


@tool
def time_tool() -> str:
    """查询当前时间和日期(年月日时分秒+星期)。

    当需要知道当前时间时调用此工具。
    返回东八区(中国标准时间)的当前时间。

    Returns:
        str: 格式化的时间字符串，格式 "YYYY-MM-DD HH:MM:SS 星期X"

    Examples:
        >>> time_tool.invoke()
        "2024-08-07 14:30:25 星期三"
        >>> time_tool.invoke()
        "2024-08-07 22:45:10 星期三"
    """
    return get_time()


if __name__ == "__main__":
    import asyncio

    async def test():
        s = get_time()
        assert "周" in s and len(s) > 15, f"get_time 输出异常: {s}"
        print(f"get_time ok: {s}")

    asyncio.run(test())


__all__ = ["get_time", "time_tool", "TimeResult"]

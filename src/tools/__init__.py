# src/tools: 工具模块 - 每个工具独立文件，包含裸函数和LangChain装饰器

from src.tools.weather import get_weather, weather_tool
from src.tools.calculator import calculate, calc_tool
from src.tools.search import web_search, web_search_tool
from src.tools.time import get_time, time_tool
from src.tools.kb_search import search_kb_tool

# ReAct Agent工具集
REACT_AGENT_TOOLS = [search_kb_tool, weather_tool, calc_tool, web_search_tool, time_tool]

__all__ = [
    "get_weather", "weather_tool",
    "calculate", "calc_tool",
    "web_search", "web_search_tool",
    "get_time", "time_tool",
    "search_kb_tool",
    "REACT_AGENT_TOOLS",
]

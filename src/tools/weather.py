# 天气工具 - 优化版

import httpx
from typing import Optional
from pydantic import BaseModel, Field

from langchain_core.tools import tool


# ponytail: 天气查询结果结构化输出
class WeatherResult(BaseModel):
    """天气查询结果结构化输出"""
    city: str = Field(description="城市名称")
    temperature: float = Field(description="当前温度(摄氏度)")
    condition: str = Field(description="天气状况")
    success: bool = Field(description="是否成功")
    error: Optional[str] = Field(default=None, description="错误信息")


# ponytail: WMO weather_code→中文, 只列常见码, 未列出回落"未知"
_WMO_CN = {
    0: "晴", 1: "晴", 2: "多云", 3: "阴", 45: "雾", 48: "雾",
    51: "小雨", 53: "小雨", 61: "雨", 63: "雨", 71: "雪", 73: "雪", 95: "雷雨"
}

# ponytail: Open-Meteo geocoding 不支持中文name(实测返回空results), 常用中文城市→英文映射兜底
_CN2EN = {
    "北京": "Beijing", "上海": "Shanghai", "广州": "Guangzhou", "深圳": "Shenzhen",
    "成都": "Chengdu", "杭州": "Hangzhou", "南京": "Nanjing", "武汉": "Wuhan",
    "西安": "Xian", "重庆": "Chongqing", "天津": "Tianjin", "苏州": "Suzhou",
    "长沙": "Changsha", "郑州": "Zhengzhou", "青岛": "Qingdao", "大连": "Dalian",
    "厦门": "Xiamen", "昆明": "Kunming", "哈尔滨": "Harbin", "沈阳": "Shenyang"
}


async def get_weather(city: str) -> str:
    """查询城市实时天气(温度、天气状况)。

    使用Open-Meteo API获取实时天气数据，免费无需API密钥。
    支持中英文城市名，常用中文城市自动映射到英文。

    Args:
        city: 城市名称(支持中文或英文)，如 "北京" 或 "Beijing"

    Returns:
        str: 天气信息，格式为 "城市名：温度°C，天气状况"，或错误信息

    Examples:
        >>> await get_weather("北京")
        "北京：32.6°C，晴"
        >>> await get_weather("Tokyo")
        "Tokyo：28.3°C，多云"
    """
    city = _CN2EN.get(city, city)  # 中文常用城市→英文, geocoding为英文索引
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # 第一步：geocoding获取城市坐标
            geo_response = await client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": city, "count": 1, "language": "zh"}
            )
            geo_data = geo_response.json()

            if not geo_data.get("results"):
                return f"(找不到城市: {city})"

            location = geo_data["results"][0]

            # 第二步：获取实时天气
            weather_response = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": location["latitude"],
                    "longitude": location["longitude"],
                    "current": "temperature_2m,weather_code"
                }
            )
            weather_data = weather_response.json()
            current = weather_data["current"]

            return f"{location['name']}：{current['temperature_2m']}°C，{_WMO_CN.get(current['weather_code'], '未知')}"

    except httpx.TimeoutException:
        return "(天气服务请求超时)"
    except Exception:
        return "(天气服务暂不可用)"


@tool
async def weather_tool(city: str) -> str:
    """查询指定城市的实时天气(温度、天气状况)。

    支持中英文城市名，常用中文城市自动映射到英文。
    使用免费的Open-Meteo API，无需API密钥。

    Args:
        city: 城市名称(支持中文或英文)，如 "北京" 或 "Beijing"

    Returns:
        str: 天气信息，格式为 "城市名：温度°C，天气状况"，或错误信息

    Examples:
        >>> await weather_tool.invoke("北京")
        "北京：32.6°C，晴"
        >>> await weather_tool.invoke("Tokyo")
        "Tokyo：28.3°C，多云"
    """
    return await get_weather(city)


__all__ = ["get_weather", "weather_tool", "WeatherResult"]

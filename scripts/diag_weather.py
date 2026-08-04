import asyncio
from src.agents.tools import get_weather

async def main():
    for city in ["北京", "Shanghai", "不存在的城市XYZ"]:
        r = await get_weather(city)
        print(f"{city} -> {r}")
    # ponytail: 可达性已知(英文geocoding 200), 北京经_CN2EN转Beijing应得温度
    r = await get_weather("北京")
    assert r and ("°C" in r or "找不到" in r or "暂不可用" in r), f"异常返回: {r}"
    print("get_weather ok")

asyncio.run(main())

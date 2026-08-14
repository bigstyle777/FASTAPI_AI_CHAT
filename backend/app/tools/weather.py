"""
计算器工具

一个工具模块只需要提供两样东西:
    TOOLS         - 给模型看的 JSON Schema（模型据此决定调哪个工具、传什么参数）
    TOOL_REGISTRY - 给代码看的 name -> 函数 映射（真正执行时按名字找到函数）

新增工具时复制本文件，改掉函数、schema 和注册表即可。
"""

import requests


def weather(city: str) -> dict:
    # 1. 城市名称 → 经纬度
    geo_response = requests.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={
            "name": city,
            "count": 1,
            "language": "zh",
            "format": "json",
        },
        timeout=5,
    )

    geo_response.raise_for_status()

    geo_data = geo_response.json()

    results = geo_data.get("results")

    if not results:
        raise ValueError(f"找不到城市: {city}")

    location = results[0]

    latitude = location["latitude"]
    longitude = location["longitude"]

    # 2. 经纬度 → 天气
    weather_response = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": latitude,
            "longitude": longitude,
            "current": "temperature_2m,weather_code,wind_speed_10m",
            "timezone": "auto",
        },
        timeout=5,
    )

    weather_response.raise_for_status()

    weather_data = weather_response.json()
    current = weather_data["current"]

    return {
        "city": location["name"],
        "country": location.get("country"),
        "latitude": latitude,
        "longitude": longitude,
        "temperature": current["temperature_2m"],
        "weather_code": current["weather_code"],
        "wind_speed": current["wind_speed_10m"],
        "time": current["time"],
    }


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "weather",
            "description": "获取天气信息",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "城市名称"},
                },
                "required": ["city"],
                "additionalProperties": False,
            },
        },
    }
]


TOOL_REGISTRY = {
    "weather": weather,
}

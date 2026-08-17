"""
计算器工具

一个工具模块只需要提供两样东西:
    TOOLS         - 给模型看的 JSON Schema（模型据此决定调哪个工具、传什么参数）
    TOOL_REGISTRY - 给代码看的 name -> 函数 映射（真正执行时按名字找到函数）

新增工具时复制本文件，改掉函数、schema 和注册表即可。
"""

from tavily import TavilyClient

from ..core.config import settings

client = TavilyClient(api_key=settings.tavily_api_key)


def web_search(query: str) -> dict:
    response = client.search(
        query=query, search_depth="basic", max_results=5, include_answer=True
    )

    return {
        "query": query,
        "results": [
            {
                "title": result.get("title"),
                "url": result.get("url"),
                "content": result.get("content"),
                "answer": result.get("answer"),
            }
            for result in response.get("results", [])
        ],
    }


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "搜索互联网，获取最新信息或外部网页内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "需要搜索的问题或关键词",
                    }
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    }
]


TOOL_REGISTRY = {
    "web_search": web_search,
}

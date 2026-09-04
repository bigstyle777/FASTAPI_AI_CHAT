def search_memory(query: str, top_k: int = 5, db=None, user_id=None) -> dict:
    # 函数内延迟导入：避免 tools 和 memory 互相导入时产生循环依赖
    from ..memory.retriever import retrieve_memories

    if db is None or user_id is None:
        return {"query": query, "hits": []}

    hits = retrieve_memories(db, query, user_id, top_k=top_k)

    return {
        "query": query,
        "hits": [
            {
                "memory_id": hit.id,
                "type": hit.type.value,
                "content": hit.content,
                "score": hit.score,
            }
            for hit in hits
        ],
    }


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_memory",
            "description": (
                "检索用户的长期记忆库（向量 + 精确匹配 + 同会话混合）。"
                "当用户提到自己的偏好、习惯、目标、长期项目、之前约定等个人化信息，"
                "或你的回答需要这些背景时调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "想查询的记忆关键词或问题",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "返回条数，默认 5",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    }
]


TOOL_REGISTRY = {
    "search_memory": search_memory,
}

def search_memory(query: str, top_k: int = 5, db=None, user_id=None) -> dict:
    # 函数内延迟导入：避免 tools 和 services 互相导入时产生循环依赖
    from ..services.memory_embedding import retrieve_relevant_memories

    hits = retrieve_relevant_memories(db, user_id, query, top_k)
    return {
        "query": query,
        "hits": [
            {
                "memory_id": hit.memory_id,
                "content": hit.content,
                "sentence": hit.sentence,
                "score": round(hit.score, 4),
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
                "检索用户的长期记忆库（向量检索）。当用户提到自己的偏好、习惯、目标、"
                "长期项目、编程技术栈、之前约定等个人化信息，或你的回答需要这些背景时调用。"
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

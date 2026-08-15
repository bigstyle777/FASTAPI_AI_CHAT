"""
RAG 外部资料检索工具

让 AI 自己判断：问题依赖用户上传的文档/外部资料时，
就调用 search_rag 去向量数据库检索相关片段。
"""

from ..core.config import settings
from ..rag.retriever import retrieve_relevant_chunks


def search_rag(query: str, top_k: int = 5, db=None, user_id=None) -> dict:
    if not settings.rag_enabled:
        return {"query": query, "hits": [], "note": "RAG 功能未启用"}

    hits = retrieve_relevant_chunks(db, user_id, query, top_k)
    return {
        "query": query,
        "hits": [
            {
                "document_id": hit.document_id,
                "chunk_id": hit.chunk_id,
                "filename": hit.filename,
                "content": hit.content,
                "score": round(hit.score, 4),
            }
            for hit in hits
        ],
    }


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_rag",
            "description": (
                "检索用户上传的外部文档向量库（RAG）。当问题需要依据用户上传的文档、"
                "资料或知识库内容回答时调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "想检索的问题或关键词",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "返回片段条数，默认 5",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    }
]


TOOL_REGISTRY = {
    "search_rag": search_rag,
}

from __future__ import annotations

from hashlib import sha256

try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None

from sqlalchemy.orm import Session

from ..core.config import settings
from ..user.repository import get_user_settings


class EmbeddingUnavailable(RuntimeError):
    """embedding 服务不可用（未配 API Key / 缺少 openai 依赖）。

    只在调用方显式传 allow_fallback=False 时抛出，让它自己决定怎么降级
    （记忆模块的选择是存 NULL、等下一轮回填）。
    """


def resolve_embedding_api_key(user_id: int, db: Session) -> str | None:
    user_settings = get_user_settings(db, user_id)
    if user_settings and user_settings.embedding_api_key:
        return user_settings.embedding_api_key
    return settings.rag_embedding_api_key


def resolve_embedding_base_url(user_id: int, db: Session) -> str:
    user_settings = get_user_settings(db, user_id)
    if user_settings and user_settings.embedding_base_url:
        return user_settings.embedding_base_url
    return settings.rag_embedding_base_url


def resolve_embedding_model(user_id: int, db: Session) -> str:
    user_settings = get_user_settings(db, user_id)
    if user_settings and user_settings.embedding_model:
        return user_settings.embedding_model
    return settings.rag_embedding_model


def embed_texts(
    texts: list[str],
    user_id: int,
    db: Session,
    *,
    allow_fallback: bool = True,
) -> list[list[float]]:
    """算一批文本的向量。

    allow_fallback=False 时，凭据缺失 / SDK 不可用会抛
    EmbeddingUnavailable 而不是返回本地哈希伪向量。记忆模块必须用
    False：伪向量存进库比"没有向量"更糟 —— 它占着 embedding 列不为
    NULL，回填逻辑（只找 NULL）永远不会再看它一眼，而检索已经在拿
    噪声排序。RAG 上传路径保留 fallback（默认 True），让本地开发在没配
    Key 时仍能跑通流程。
    """
    if not texts:
        return []

    api_key = resolve_embedding_api_key(user_id, db)
    if OpenAI is None or not api_key:
        if not allow_fallback:
            raise EmbeddingUnavailable(
                "embedding 服务不可用（未配置 API Key 或缺少 openai 依赖）"
            )
        return [_fallback_embedding(text) for text in texts]

    client = OpenAI(
        api_key=api_key,
        base_url=resolve_embedding_base_url(user_id, db),
    )
    response = client.embeddings.create(
        model=resolve_embedding_model(user_id, db),
        input=texts,
    )

    embeddings = [item.embedding for item in response.data]
    for embedding in embeddings:
        if len(embedding) != settings.rag_embedding_dimension:
            raise ValueError(
                "Embedding dimension does not match rag_embedding_dimension",
            )
    return embeddings


def embed_query(
    query: str,
    user_id: int,
    db: Session,
    *,
    allow_fallback: bool = True,
) -> list[float]:
    return embed_texts([query], user_id, db, allow_fallback=allow_fallback)[0]


def _fallback_embedding(text: str) -> list[float]:
    """Provide a deterministic local fallback so uploads still work in dev."""
    dimension = settings.rag_embedding_dimension
    vector = [0.0] * dimension
    digest = sha256(text.encode("utf-8")).digest()

    for index, byte in enumerate(digest * (dimension // len(digest) + 1)):
        if index >= dimension:
            break
        vector[index] = (byte / 255.0) * 2.0 - 1.0
    return vector


def looks_like_fallback_vector(vector) -> bool:
    """判断一个向量是不是 _fallback_embedding 造出的伪向量。

    伪向量每个分量都是 (byte / 255) * 2 - 1，即落在 (2k - 255) / 255 这张
    离散网格上；真实模型输出的浮点分量不可能全部命中。只做数值检查、
    不需要原文，所以能在不重算 embedding 的前提下把历史污染行筛出来
    （见 alembic 的 clear_fallback_memory_embeddings）。

    容差取 1e-3：pgvector 存 float4，往返精度损失在 1e-5 量级。
    全零向量不会被误判（0 映射到 127.5，不在网格点上）。
    """
    if vector is None:
        return False
    values = list(vector)
    if not values:
        return False
    for value in values:
        scaled = (float(value) + 1.0) * 255.0 / 2.0
        if abs(scaled - round(scaled)) > 1e-3:
            return False
    return True

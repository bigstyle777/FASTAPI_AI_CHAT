from __future__ import annotations

from hashlib import sha256

try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None

from sqlalchemy.orm import Session

from ..core.config import settings
from ..crud import get_user_settings


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


def embed_texts(texts: list[str], user_id: int, db: Session) -> list[list[float]]:
    if not texts:
        return []

    api_key = resolve_embedding_api_key(user_id, db)
    if OpenAI is None or not api_key:
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


def embed_query(query: str, user_id: int, db: Session) -> list[float]:
    return embed_texts([query], user_id, db)[0]


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

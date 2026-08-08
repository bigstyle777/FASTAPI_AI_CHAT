try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None

from sqlalchemy.orm import Session

from ..core.config import settings
from ..crud import get_user_settings
from ..exceptions import BusinessError


def embed_texts(texts: list[str], user_id: int, db: Session) -> list[list[float]]:
    if not texts:
        return []

    api_key = _get_embedding_api_key(user_id, db)
    if OpenAI is None or not api_key:
        raise BusinessError("RAG 向量化需要配置 OpenAI API Key")

    client = OpenAI(api_key=api_key, base_url=settings.openai_base_url)
    response = client.embeddings.create(
        model=settings.rag_embedding_model,
        input=texts,
    )
    embeddings = [item.embedding for item in response.data]
    for embedding in embeddings:
        if len(embedding) != settings.rag_embedding_dimension:
            raise BusinessError(
                "Embedding 维度与 RAG_EMBEDDING_DIMENSION 不一致，请检查配置"
            )
    return embeddings


def embed_query(query: str, user_id: int, db: Session) -> list[float]:
    return embed_texts([query], user_id, db)[0]


def _get_embedding_api_key(user_id: int, db: Session) -> str | None:
    row = get_user_settings(db, user_id)
    if row and row.provider == "openai" and row.api_key:
        return row.api_key
    return settings.openai_api_key

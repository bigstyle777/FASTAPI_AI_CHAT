from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import RagChunk, RagChunkEmbedding, RagDocument
from .embedding import embed_query


@dataclass(frozen=True)
class RetrievalHit:
    document_id: int
    chunk_id: int
    filename: str
    content: str
    score: float


def retrieve_relevant_chunks(
    db: Session,
    user_id: int,
    query: str,
    top_k: int,
) -> list[RetrievalHit]:
    query_vector = embed_query(query, user_id, db)
    distance = RagChunkEmbedding.embedding.cosine_distance(query_vector)
    stmt = (
        select(
            RagDocument.id.label("document_id"),
            RagChunk.id.label("chunk_id"),
            RagDocument.filename,
            RagChunk.content,
            distance.label("distance"),
        )
        .join(RagChunk, RagChunk.document_id == RagDocument.id)
        .join(RagChunkEmbedding, RagChunkEmbedding.chunk_id == RagChunk.id)
        .where(
            RagDocument.user_id == user_id,
            RagDocument.status == "ready",
        )
        .order_by(distance.asc())
        .limit(top_k)
    )
    rows = db.execute(stmt).all()
    return [
        RetrievalHit(
            document_id=row.document_id,
            chunk_id=row.chunk_id,
            filename=row.filename,
            content=row.content,
            score=max(0.0, 1.0 - float(row.distance)),
        )
        for row in rows
    ]

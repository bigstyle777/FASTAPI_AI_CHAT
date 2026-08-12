from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import RagChunk, RagChunkEmbedding, RagDocument


def create_document(
    db: Session,
    user_id: int,
    filename: str,
    storage_path: str,
    mime_type: str | None,
    file_size: int,
    doc_hash: str,
) -> RagDocument:
    document = RagDocument(
        user_id=user_id,
        filename=filename,
        storage_path=storage_path,
        mime_type=mime_type,
        file_size=file_size,
        doc_hash=doc_hash,
        status="pending",
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


def list_documents(db: Session, user_id: int) -> list[RagDocument]:
    stmt = (
        select(RagDocument)
        .where(RagDocument.user_id == user_id)
        .order_by(RagDocument.created_at.desc(), RagDocument.id.desc())
    )
    return db.execute(stmt).scalars().all()


def get_document_by_user(
    db: Session,
    document_id: int,
    user_id: int,
) -> RagDocument | None:
    stmt = select(RagDocument).where(
        RagDocument.id == document_id,
        RagDocument.user_id == user_id,
    )
    return db.execute(stmt).scalar_one_or_none()


def delete_document(db: Session, document: RagDocument) -> None:
    db.delete(document)
    db.commit()


def replace_document_chunks(
    db: Session,
    document: RagDocument,
    chunks,
    embeddings: list[list[float]],
    model: str,
    dimension: int,
) -> None:
    db.execute(delete(RagChunk).where(RagChunk.document_id == document.id))

    for chunk, embedding_vector in zip(chunks, embeddings, strict=True):
        row = RagChunk(
            document_id=document.id,
            chunk_index=chunk.index,
            content=chunk.content,
            token_count=chunk.token_count,
            content_hash=chunk.content_hash,
        )
        db.add(row)
        db.flush()
        db.add(
            RagChunkEmbedding(
                chunk_id=row.id,
                model=model,
                dimension=dimension,
                embedding=embedding_vector,
            )
        )

    document.status = "ready"
    document.chunk_count = len(chunks)
    document.embedding_model = model
    document.error_message = None
    document.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(document)


def mark_document_failed(db: Session, document: RagDocument, message: str) -> None:
    document.status = "failed"
    document.error_message = message[:2000]
    document.updated_at = datetime.utcnow()
    db.commit()


def mark_document_processing(db: Session, document: RagDocument) -> None:
    document.status = "processing"
    document.error_message = None
    document.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(document)

from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile
from sqlalchemy.orm import Session

from ..core.config import PROJECT_ROOT, settings
from ..exceptions import BusinessError
from .crud import (
    create_document,
    delete_document,
    get_document_by_user,
    list_documents,
    mark_document_failed,
    replace_document_chunks,
)
from .embedding import embed_texts
from .loader import load_text_from_file
from .prompts import build_context_message
from .retriever import retrieve_relevant_chunks
from .splitter import split_text


def list_documents_service(db: Session, user) -> dict:
    return {
        "success": True,
        "documents": [
            _serialize_document(document)
            for document in list_documents(db, user["user_id"])
        ],
    }


def upload_document_service(db: Session, user, file: UploadFile) -> dict:
    content = file.file.read()
    if not content:
        raise BusinessError("上传文件为空")

    upload_dir = _resolve_upload_dir()
    upload_dir.mkdir(parents=True, exist_ok=True)

    safe_name = _safe_filename(file.filename or "document.txt")
    storage_name = f"{uuid4().hex}_{safe_name}"
    storage_path = upload_dir / storage_name
    storage_path.write_bytes(content)

    document = create_document(
        db=db,
        user_id=user["user_id"],
        filename=safe_name,
        storage_path=str(storage_path),
        mime_type=file.content_type,
        file_size=len(content),
        doc_hash=sha256(content).hexdigest(),
    )

    try:
        index_document(db, user["user_id"], document.id)
    except BusinessError as error:
        mark_document_failed(db, document, str(error.message))
    except Exception as error:  # noqa: BLE001
        mark_document_failed(db, document, str(error))

    db.refresh(document)
    return {"success": True, "document": _serialize_document(document)}


def delete_document_service(db: Session, user, document_id: int) -> dict:
    document = get_document_by_user(db, document_id, user["user_id"])
    if not document:
        raise BusinessError("文档不存在")

    storage_path = Path(document.storage_path)
    delete_document(db, document)
    if storage_path.exists() and storage_path.is_file():
        storage_path.unlink()
    return {"success": True, "message": "文档已删除"}


def search_documents_service(db: Session, user, query: str) -> dict:
    query = query.strip()
    if not query:
        raise BusinessError("检索关键词不能为空")

    hits = retrieve_relevant_chunks(db, user["user_id"], query, settings.rag_top_k)
    return {
        "success": True,
        "hits": [
            {
                "document_id": hit.document_id,
                "chunk_id": hit.chunk_id,
                "filename": hit.filename,
                "content": hit.content,
                "score": hit.score,
            }
            for hit in hits
        ],
    }


def index_document(db: Session, user_id: int, document_id: int) -> None:
    document = get_document_by_user(db, document_id, user_id)
    if not document:
        raise BusinessError("文档不存在")

    text = load_text_from_file(document.storage_path, document.filename)
    chunks = split_text(
        text,
        chunk_size=settings.rag_chunk_size,
        overlap=settings.rag_chunk_overlap,
    )
    if not chunks:
        raise BusinessError("文档没有可索引内容")

    # 批量 embedding 可以减少网络往返，失败时整篇文档标记为 failed。
    vectors = embed_texts([chunk.content for chunk in chunks], user_id, db)
    replace_document_chunks(
        db=db,
        document=document,
        chunks=chunks,
        embeddings=vectors,
        model=settings.rag_embedding_model,
        dimension=settings.rag_embedding_dimension,
    )


def augment_messages_with_rag(
    db: Session,
    user_id: int,
    user_message: str,
    messages: list[dict[str, str]],
) -> list[dict[str, str]]:
    if not settings.rag_enabled:
        return messages

    try:
        hits = retrieve_relevant_chunks(db, user_id, user_message, settings.rag_top_k)
    except Exception:
        # RAG 是增强链路，检索失败不应该阻断基础聊天。
        return messages

    context_message = build_context_message(hits, settings.rag_max_context_chars)
    if not context_message:
        return messages

    return [context_message, *messages]


def _resolve_upload_dir() -> Path:
    path = Path(settings.rag_upload_dir)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def _safe_filename(filename: str) -> str:
    name = Path(filename).name.strip().replace("\x00", "")
    return name or "document.txt"


def _serialize_document(document) -> dict:
    return {
        "document_id": document.id,
        "filename": document.filename,
        "mime_type": document.mime_type,
        "file_size": document.file_size,
        "status": document.status,
        "chunk_count": document.chunk_count,
        "embedding_model": document.embedding_model,
        "error_message": document.error_message,
        "created_at": _format_dt(document.created_at),
        "updated_at": _format_dt(document.updated_at),
    }


def _format_dt(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.isoformat(timespec="seconds")

import logging
from hashlib import sha256
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from fastapi import UploadFile
from sqlalchemy.orm import Session

from ..core.config import PROJECT_ROOT, settings
from ..core.sse import sse_event
from ..exceptions import BusinessError
from .crud import (
    create_document,
    delete_document,
    get_document_by_user,
    list_documents,
    mark_document_failed,
    mark_document_processing,
    replace_document_chunks,
)
from .embedding import embed_texts, resolve_embedding_model
from .loader import load_text_from_file
from .prompts import build_context_message
from .retriever import retrieve_relevant_chunks
from .splitter import split_text

logger = logging.getLogger(__name__)


def list_documents_service(db: Session, user) -> dict:
    documents = list_documents(db, user["user_id"])
    return {
        "success": True,
        "documents": [_serialize_document(document) for document in documents],
    }


def stream_upload_document_service(
    db: Session,
    user,
    file: UploadFile,
) -> Iterator[str]:
    document = None
    storage_path: Path | None = None

    try:
        user_id = user["user_id"]
        filename = _safe_filename(file.filename or "document.txt")

        yield _progress("validating", "正在校验文件")
        content = file.file.read()
        if not content:
            raise BusinessError("上传文件为空")

        yield _progress("saving", "正在保存文件")
        upload_dir = _resolve_upload_dir()
        upload_dir.mkdir(parents=True, exist_ok=True)
        storage_path = upload_dir / f"{uuid4().hex}_{filename}"
        storage_path.write_bytes(content)

        document = create_document(
            db=db,
            user_id=user_id,
            filename=filename,
            storage_path=str(storage_path),
            mime_type=file.content_type,
            file_size=len(content),
            doc_hash=sha256(content).hexdigest(),
        )
        mark_document_processing(db, document)
        yield _progress("saved", "文件已保存", _serialize_document(document))

        for stage, message in _index_document_steps(db, user_id, document):
            yield _progress(stage, message)

        db.refresh(document)
        yield sse_event("done", {"document": _serialize_document(document)})
    except BusinessError as error:
        _mark_failed(db, document, str(error.message))
        _delete_untracked_file(document, storage_path)
        yield sse_event("error", {"message": str(error.message)})
    except Exception as error:
        logger.exception("RAG upload failed")
        _mark_failed(db, document, str(error))
        _delete_untracked_file(document, storage_path)
        yield sse_event("error", {"message": f"文档处理失败：{error}"})


def enqueue_document_processing(document_id: int, user_id: int) -> bool:
    try:
        from .tasks import process_document_task

        process_document_task.apply_async(args=(document_id, user_id), retry=False)
        return True
    except Exception:
        logger.exception("Failed to enqueue RAG document processing")
        return False


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


def process_document(db: Session, user_id: int, document_id: int) -> dict:
    document = get_document_by_user(db, document_id, user_id)
    if not document:
        raise BusinessError("文档不存在")

    mark_document_processing(db, document)
    try:
        index_document(db, user_id, document_id)
    except BusinessError as error:
        mark_document_failed(db, document, str(error.message))
        raise
    except Exception as error:
        mark_document_failed(db, document, str(error))
        raise

    db.refresh(document)
    return _serialize_document(document)


def index_document(db: Session, user_id: int, document_id: int) -> None:
    document = get_document_by_user(db, document_id, user_id)
    if not document:
        raise BusinessError("文档不存在")

    for _ in _index_document_steps(db, user_id, document):
        pass


def _index_document_steps(db: Session, user_id: int, document) -> Iterator[tuple[str, str]]:
    """解析→切分→向量化→写索引的共用管线；yield (stage, message) 进度。

    流式上传和后台索引共用这一份顺序，保证两条路径的阶段永远一致：
    以后新增/调整索引步骤只需改这里，两条路径连同进度上报一起生效。
    """
    yield "parsing", "正在解析文档"
    text = load_text_from_file(document.storage_path, document.filename)

    yield "chunking", "正在切分文档"
    chunks = split_text(
        text,
        chunk_size=settings.rag_chunk_size,
        overlap=settings.rag_chunk_overlap,
    )
    if not chunks:
        raise BusinessError("文档没有可索引内容")

    yield "embedding", f"正在生成 {len(chunks)} 个文本块的向量"
    vectors = embed_texts([chunk.content for chunk in chunks], user_id, db)

    yield "indexing", "正在写入 pgvector 索引"
    embedding_model = resolve_embedding_model(user_id, db)
    replace_document_chunks(
        db=db,
        document=document,
        chunks=chunks,
        embeddings=vectors,
        model=embedding_model,
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
        return messages

    context_message = build_context_message(hits, settings.rag_max_context_chars)
    return [context_message, *messages] if context_message else messages


def _progress(
    stage: str,
    message: str,
    document: dict | None = None,
) -> str:
    payload = {"stage": stage, "message": message}
    if document is not None:
        payload["document"] = document
    return sse_event("progress", payload)


def _mark_failed(db: Session, document, message: str) -> None:
    if document is not None:
        mark_document_failed(db, document, message)


def _delete_untracked_file(document, storage_path: Path | None) -> None:
    if document is None and storage_path and storage_path.exists():
        storage_path.unlink()


def _resolve_upload_dir() -> Path:
    path = Path(settings.rag_upload_dir)
    return path if path.is_absolute() else PROJECT_ROOT / path


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

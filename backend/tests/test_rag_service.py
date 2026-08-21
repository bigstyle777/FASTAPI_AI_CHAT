"""RAG 索引管线测试（真实测试库 + 假 embedding，不依赖外部 API）。

覆盖：
    index_document                    解析→切分→向量化→写索引 全链路
    process_document                  状态流转（processing → ready / failed）
    stream_upload_document_service    流式上传的阶段事件 / 错误清理
    delete_document_service           删除文档连带物理文件与 chunk 行

用法（在 backend 目录下运行）：
    ..\\.venv\\Scripts\\python.exe -m pytest tests\\test_rag_service.py -v
"""

import io
import json
import sys
from hashlib import sha256
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi import UploadFile  # noqa: E402
from sqlalchemy import select  # noqa: E402

import app.rag.service as rag_service  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.crud import create_role, create_user, get_role_by_name  # noqa: E402
from app.exceptions import BusinessError  # noqa: E402
from app.models import RagChunk, RagChunkEmbedding, RagDocument  # noqa: E402
from app.rag.crud import create_document  # noqa: E402
from app.rag.service import (  # noqa: E402
    delete_document_service,
    index_document,
    process_document,
    stream_upload_document_service,
)


def _make_user_id(db):
    role = get_role_by_name(db, "user")
    if role is None:
        role = create_role(db, "user")
    return create_user(db, "rag_tester", "hashed-password", role.id).id


def _make_document(db, user_id, tmp_path, content: str, filename="notes.txt"):
    path = tmp_path / filename
    path.write_text(content, encoding="utf-8")
    return create_document(
        db=db,
        user_id=user_id,
        filename=filename,
        storage_path=str(path),
        mime_type="text/plain",
        file_size=len(content.encode("utf-8")),
        doc_hash=sha256(content.encode("utf-8")).hexdigest(),
    )


def _load_chunks(db, document_id):
    stmt = select(RagChunk).where(RagChunk.document_id == document_id).order_by(RagChunk.chunk_index)
    return list(db.execute(stmt).scalars().all())


def _load_embeddings(db, chunk_ids):
    stmt = select(RagChunkEmbedding).where(RagChunkEmbedding.chunk_id.in_(chunk_ids))
    return list(db.execute(stmt).scalars().all())


def _parse_sse(event_str: str) -> tuple[str, dict]:
    lines = event_str.strip().split("\n")
    event_name = lines[0].removeprefix("event: ")
    payload = json.loads(lines[1].removeprefix("data: "))
    return event_name, payload


@pytest.fixture
def rag_env(tmp_path, monkeypatch):
    """上传目录指向临时目录；embed_texts 换成确定性假向量（不访问外部 API）。"""
    monkeypatch.setattr(settings, "rag_upload_dir", str(tmp_path / "rag-uploads"))

    def fake_embed_texts(texts, user_id, db):
        return [[0.01] * settings.rag_embedding_dimension for _ in texts]

    monkeypatch.setattr(rag_service, "embed_texts", fake_embed_texts)
    return tmp_path


# ---------------------------------------------------------------------------
# index_document / process_document
# ---------------------------------------------------------------------------


def test_index_document_creates_chunks_and_embeddings(db, rag_env, tmp_path):
    user_id = _make_user_id(db)
    document = _make_document(db, user_id, tmp_path, "第一段内容。\n\n第二段内容。")

    index_document(db, user_id, document.id)

    # 短段落会被打包进同一个 chunk（chunk_size 以内）
    chunks = _load_chunks(db, document.id)
    assert [c.content for c in chunks] == ["第一段内容。\n\n第二段内容。"]

    embeddings = _load_embeddings(db, [c.id for c in chunks])
    assert len(embeddings) == 1
    assert all(e.dimension == settings.rag_embedding_dimension for e in embeddings)

    db.refresh(document)
    assert document.status == "ready"
    assert document.chunk_count == 1
    assert document.embedding_model == settings.rag_embedding_model


def test_index_document_missing_document_raises(db, rag_env):
    user_id = _make_user_id(db)
    with pytest.raises(BusinessError):
        index_document(db, user_id, 999999)


def test_index_document_replaces_old_chunks(db, rag_env, tmp_path):
    """重复索引同一文档应先清空旧 chunk，而不是叠加。"""
    user_id = _make_user_id(db)
    # 超过 chunk_size 的长文本会被切出多个 chunk（900 上限 + 150 重叠）
    long_text = "A" * 1000
    document = _make_document(db, user_id, tmp_path, long_text)
    index_document(db, user_id, document.id)
    first_count = len(_load_chunks(db, document.id))
    assert first_count == 2

    index_document(db, user_id, document.id)

    assert len(_load_chunks(db, document.id)) == first_count
    assert document.status == "ready"


def test_process_document_marks_failed_on_empty_content(db, rag_env, tmp_path):
    user_id = _make_user_id(db)
    document = _make_document(db, user_id, tmp_path, "   \n\n  ")

    with pytest.raises(BusinessError):
        process_document(db, user_id, document.id)

    db.refresh(document)
    assert document.status == "failed"
    assert document.error_message


def test_process_document_success_flow(db, rag_env, tmp_path):
    user_id = _make_user_id(db)
    document = _make_document(db, user_id, tmp_path, "可以被索引的内容")

    result = process_document(db, user_id, document.id)

    assert result["status"] == "ready"
    assert result["chunk_count"] == 1


# ---------------------------------------------------------------------------
# stream_upload_document_service
# ---------------------------------------------------------------------------


def test_stream_upload_document_service_end_to_end(db, rag_env):
    user_id = _make_user_id(db)
    content = "RAG 索引管线测试。\n\n第二段内容在这里。"
    upload = UploadFile(file=io.BytesIO(content.encode("utf-8")), filename="demo.txt")

    events = [_parse_sse(e) for e in stream_upload_document_service(db, {"user_id": user_id}, upload)]

    # 阶段序列完整，最后是 done
    stages = [payload["stage"] for name, payload in events if name == "progress"]
    assert stages == [
        "validating",
        "saving",
        "saved",
        "parsing",
        "chunking",
        "embedding",
        "indexing",
    ]
    assert events[-1][0] == "done"

    document = db.execute(select(RagDocument)).scalar_one()
    assert document.status == "ready"
    assert document.user_id == user_id
    assert len(_load_chunks(db, document.id)) == 1

    # 物理文件落在临时上传目录
    storage = Path(document.storage_path)
    assert storage.exists()
    assert rag_env.name in storage.parts


def test_stream_upload_empty_file_reports_error_and_cleans_up(db, rag_env):
    user_id = _make_user_id(db)
    upload = UploadFile(file=io.BytesIO(b""), filename="empty.txt")

    events = [_parse_sse(e) for e in stream_upload_document_service(db, {"user_id": user_id}, upload)]

    assert events[-1][0] == "error"
    assert "空" in events[-1][1]["message"]
    # 没有创建任何文档行，也没有残留文件
    assert db.execute(select(RagDocument)).scalars().all() == []
    upload_dir = Path(settings.rag_upload_dir)
    leftover = list(upload_dir.glob("*")) if upload_dir.exists() else []
    assert leftover == []


def test_stream_upload_unsupported_suffix_reports_error(db, rag_env):
    user_id = _make_user_id(db)
    upload = UploadFile(file=io.BytesIO(b"whatever"), filename="demo.pdf")

    events = [_parse_sse(e) for e in stream_upload_document_service(db, {"user_id": user_id}, upload)]

    assert events[-1][0] == "error"


# ---------------------------------------------------------------------------
# delete_document_service
# ---------------------------------------------------------------------------


def test_delete_document_service_removes_rows_and_file(db, rag_env, tmp_path):
    user_id = _make_user_id(db)
    document = _make_document(db, user_id, tmp_path, "将被删除的内容")
    index_document(db, user_id, document.id)
    storage_path = Path(document.storage_path)
    assert storage_path.exists()

    result = delete_document_service(db, {"user_id": user_id}, document.id)

    assert result["success"] is True
    assert not storage_path.exists()
    assert _load_chunks(db, document.id) == []
    assert db.execute(select(RagDocument)).scalars().all() == []


def test_delete_document_service_missing_document_raises(db, rag_env):
    user_id = _make_user_id(db)
    with pytest.raises(BusinessError):
        delete_document_service(db, {"user_id": user_id}, 999999)

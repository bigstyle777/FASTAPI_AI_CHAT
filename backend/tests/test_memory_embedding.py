"""memory_embedding 测试（真实测试库 + 假 embedding，不依赖外部 API）。

覆盖：
    split_sentences            中英文标点分句 / 兜底整段
    embed_memory               分句向量落库
    sync_memory_embeddings     回填老数据（无向量的 memory）
    retrieve_relevant_memories 向量检索 + 按 memory 去重

假 embedding 策略：句子内容 -> 由关键词决定的向量，
让 "Python" 相关句子彼此接近、与 "火锅" 句子远离，检索可预测。

用法（在 backend 目录下运行）：
    ..\\.venv\\Scripts\\python.exe -m pytest tests\\test_memory_embedding.py -v
"""

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select  # noqa: E402

import app.services.memory_embedding as memory_embedding  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.crud import create_memory, create_role, create_user, get_role_by_name  # noqa: E402
from app.models import UserMemoryEmbedding  # noqa: E402
from app.services.memory_embedding import (  # noqa: E402
    embed_memory,
    retrieve_relevant_memories,
    split_sentences,
    sync_memory_embeddings,
)

DIMENSION = settings.rag_embedding_dimension


def _make_user_id(db, tag):
    role = get_role_by_name(db, "user")
    if role is None:
        role = create_role(db, "user")
    return create_user(db, f"{tag}_user", "hashed-password", role.id).id


@pytest.fixture(autouse=True)
def fake_embedding(monkeypatch):
    """按关键词生成可预测的向量：话题一致 -> 距离近。"""

    def vector_for(text: str) -> list[float]:
        vector = [0.0] * DIMENSION
        seeds = [ord(ch) % DIMENSION for ch in text[:16]]
        for seed in seeds:
            vector[seed % DIMENSION] = 1.0
        return vector

    def fake_embed_texts(texts, user_id, db):
        return [vector_for(t) for t in texts]

    def fake_embed_query(query, user_id, db):
        return vector_for(query)

    monkeypatch.setattr(memory_embedding, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(memory_embedding, "embed_query", fake_embed_query)


# ---------------------------------------------------------------------------
# split_sentences
# ---------------------------------------------------------------------------


def test_split_sentences_chinese_punctuation():
    assert split_sentences("第一句。第二句！第三句？") == [
        "第一句。",
        "第二句！",
        "第三句？",
    ]


def test_split_sentences_english_and_newlines():
    assert split_sentences("One. Two!\n\nThree") == ["One.", "Two!", "Three"]


def test_split_sentences_fallback_to_whole_text():
    assert split_sentences("没有分隔符的一整段") == ["没有分隔符的一整段"]
    assert split_sentences("") == [""]


# ---------------------------------------------------------------------------
# embed_memory / sync_memory_embeddings
# ---------------------------------------------------------------------------


def _load_embedding_rows(db, memory_id):
    stmt = select(UserMemoryEmbedding).where(
        UserMemoryEmbedding.memory_id == memory_id
    )
    return list(db.execute(stmt).scalars().all())


def test_embed_memory_stores_sentence_vectors(db):
    user_id = _make_user_id(db, "embed")
    memory = create_memory(db, user_id, "喜欢 Python。也在学 Rust！")

    assert embed_memory(db, user_id, memory) is True

    rows = _load_embedding_rows(db, memory.id)
    assert [r.content for r in rows] == ["喜欢 Python。", "也在学 Rust！"]
    assert all(r.dimension == DIMENSION for r in rows)
    assert all(len(r.embedding) == DIMENSION for r in rows)


def test_sync_memory_embeddings_backfills_only_missing(db):
    user_id = _make_user_id(db, "sync")
    with_vector = create_memory(db, user_id, "已有向量的记忆。")
    without_vector = create_memory(db, user_id, "等待回填的记忆。")
    embed_memory(db, user_id, with_vector)

    count = sync_memory_embeddings(db, user_id)

    assert count == 1
    assert len(_load_embedding_rows(db, with_vector.id)) == 1
    assert len(_load_embedding_rows(db, without_vector.id)) == 1

    # 再跑一次：全部已有向量，不重复回填
    assert sync_memory_embeddings(db, user_id) == 0


# ---------------------------------------------------------------------------
# retrieve_relevant_memories
# ---------------------------------------------------------------------------


def test_retrieve_relevant_memories_dedups_by_memory(db):
    user_id = _make_user_id(db, "retrieve")
    target = create_memory(db, user_id, "用户正在学习 Python 和 FastAPI。每天都写代码。")
    create_memory(db, user_id, "今天中午吃了火锅，味道还不错。")
    sync_memory_embeddings(db, user_id)

    hits = retrieve_relevant_memories(db, user_id, "Python FastAPI 学习", top_k=5)

    assert hits, "应该能检索到相关记忆"
    memory_ids = [hit.memory_id for hit in hits]
    assert len(memory_ids) == len(set(memory_ids)), "同一条 memory 只应出现一次"
    assert target.id in memory_ids
    # 命中句子应来自目标 memory
    target_hit = next(hit for hit in hits if hit.memory_id == target.id)
    assert "Python" in target_hit.sentence
    assert target_hit.score > 0


def test_retrieve_relevant_memories_respects_top_k(db):
    user_id = _make_user_id(db, "topk")
    for i in range(4):
        create_memory(db, user_id, f"关于 Python 的第 {i} 条记忆。")

    hits = retrieve_relevant_memories(db, user_id, "Python", top_k=2)

    assert len(hits) <= 2

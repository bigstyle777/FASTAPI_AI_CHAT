"""embedding 严格性测试：拿不到真向量时的行为。

背景（P0）：embed_texts 在没有 API Key 时曾返回 sha256 派生的伪向量，
记忆模块把它当真向量写进 memories.embedding。这种污染既不报错也不自愈：
embedding 不为 NULL，回填逻辑（只找 NULL）永远跳过它，而向量检索已经在
拿噪声当相似度排序。

现在记忆模块一律用 allow_fallback=False：宁可存 NULL 等回填，也不要伪向量。
RAG 上传路径保留 fallback（allow_fallback 默认 True），本地开发没配 Key
时仍能跑通流程 —— 这条兼容也要守住。

全部是纯逻辑用例，不需要数据库：
    pytest tests/test_embedding_strictness.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import _embedding_stub  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.memory import manager as memory_manager  # noqa: E402
from app.rag import embedding as rag_embedding  # noqa: E402


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


@pytest.fixture
def no_credentials(deterministic_embedding, monkeypatch):
    """把真实现 patch 回去，并让凭据解析返回 None（等价于 CI 里没配 Key）。

    显式依赖 deterministic_embedding：conftest 的 autouse fixture 会先把
    embed_* 换成假实现并把原函数记进 _embedding_stub，这里要取回原函数。
    """
    monkeypatch.setattr(
        rag_embedding, "embed_texts", _embedding_stub.original_embed_texts
    )
    monkeypatch.setattr(
        rag_embedding, "embed_query", _embedding_stub.original_embed_query
    )
    monkeypatch.setattr(
        memory_manager, "embed_texts", _embedding_stub.original_embed_texts
    )
    monkeypatch.setattr(
        rag_embedding, "resolve_embedding_api_key", lambda user_id, db: None
    )


class TestFallbackVectorDetection:
    """伪向量的数值特征：每个分量都落在 (2k - 255) / 255 网格上。"""

    def test_detects_production_fallback(self):
        vector = rag_embedding._fallback_embedding("任意文本")
        assert rag_embedding.looks_like_fallback_vector(vector)

    def test_rejects_semantic_fake_vector(self):
        """测试用的词袋假向量不该被误判（否则 migration 会清掉正常数据）。"""
        assert not rag_embedding.looks_like_fallback_vector(
            _embedding_stub.fake_vector("python fastapi 框架")
        )

    def test_rejects_arbitrary_floats(self):
        assert not rag_embedding.looks_like_fallback_vector([0.1234, -0.5678, 0.9])

    def test_zero_vector_not_flagged(self):
        assert not rag_embedding.looks_like_fallback_vector([0.0] * 8)

    def test_none_and_empty_not_flagged(self):
        assert not rag_embedding.looks_like_fallback_vector(None)
        assert not rag_embedding.looks_like_fallback_vector([])


class TestStrictMode:
    """记忆模块用的严格模式：没有真向量就报错，绝不返回伪向量。"""

    def test_embed_texts_raises(self, no_credentials):
        with pytest.raises(rag_embedding.EmbeddingUnavailable):
            rag_embedding.embed_texts(["一段文本"], 1, None, allow_fallback=False)

    def test_embed_query_raises(self, no_credentials):
        with pytest.raises(rag_embedding.EmbeddingUnavailable):
            rag_embedding.embed_query("一段查询", 1, None, allow_fallback=False)

    def test_memory_embed_text_returns_none(self, no_credentials):
        """_embed_text 把异常收敛成 None —— 记忆以待回填落库，不是伪向量。"""
        assert memory_manager._embed_text(None, 1, "用户偏好 4 空格缩进") is None

    def test_memory_embed_text_disabled_returns_none(
        self, no_credentials, monkeypatch
    ):
        monkeypatch.setattr(settings, "memory_embedding_enabled", False)
        assert memory_manager._embed_text(None, 1, "任意文本") is None


class TestLenientMode:
    """RAG 上传路径的兼容：默认仍允许本地 fallback，别把 dev 流程弄坏。"""

    def test_embed_texts_falls_back_by_default(self, no_credentials):
        vectors = rag_embedding.embed_texts(["一段文本"], 1, None)
        assert len(vectors) == 1
        assert len(vectors[0]) == settings.rag_embedding_dimension
        assert rag_embedding.looks_like_fallback_vector(vectors[0])

    def test_empty_input_short_circuits(self, no_credentials):
        assert rag_embedding.embed_texts([], 1, None, allow_fallback=False) == []


class TestStubHasSemantics:
    """守住测试替身本身的有效性：相关文本必须比不相关文本更相似。

    这条是整个记忆检索测试有意义的前提 —— 之前用 sha256 伪向量当语义
    替身时，"相关的排前面"这类断言实际是在抛硬币。
    """

    def test_related_texts_score_higher(self):
        query = _embedding_stub.fake_vector("python fastapi")
        related = _embedding_stub.fake_vector("python fastapi 框架")
        unrelated = _embedding_stub.fake_vector("中午吃了火锅")

        assert _cosine(query, related) > _cosine(query, unrelated)

    def test_identical_texts_score_one(self):
        vector = _embedding_stub.fake_vector("用户的核心目标")
        assert _cosine(vector, vector) == pytest.approx(1.0)

    def test_chinese_bigram_overlap(self):
        """中文靠 bigram 对上：含"魔法数字"的两个句子应当相关。"""
        query = _embedding_stub.fake_vector("代码里的魔法数字")
        related = _embedding_stub.fake_vector("用户多次提到讨厌魔法数字")
        unrelated = _embedding_stub.fake_vector("今天天气不错")

        assert _cosine(query, related) > _cosine(query, unrelated)

"""测试专用的确定性假 embedding。

为什么不复用生产的 _fallback_embedding
------------------------------------
那个函数按 sha256 生成向量：对"python fastapi"和"python fastapi 框架"
这种明显相关的文本，两个哈希之间毫无关系，余弦相似度是纯随机数。拿它当
语义替身，"相关的应该排前面"这类断言就变成抛硬币 —— 而 CI 里没有配
embedding Key，所有走向量路的记忆/RAG 测试实际都跑在那条 fallback 上。

这里的假向量按 token 打点再 L2 归一化：共享 token 越多，余弦越高。
足够让检索与排序测试有意义，又不需要任何外部 API。

生产侧的严格性不受影响：拿不到真向量时 embed_texts(allow_fallback=False)
会抛 EmbeddingUnavailable（记忆模块正是这么调的）。测试用 install() 把
各个绑定点替换掉，等于假装 embedding 服务永远可用。
"""

from __future__ import annotations

import re
from hashlib import sha256

from app.core.config import settings

# 英文/数字按连续片段切，汉字既取单字也取相邻二字组合 ——
# 中文没有空格分词，bigram 是"代码"/"魔法数字"这类词能对上的最小手段。
_LATIN_TOKENS = re.compile(r"[a-z0-9_]+")
_HAN_CHARS = re.compile(r"[\u4e00-\u9fff]")


def tokenize(text: str) -> list[str]:
    """把文本切成用于打点的 token（英文词 + 汉字单字 + 汉字 bigram）。"""
    lowered = (text or "").lower()
    tokens = _LATIN_TOKENS.findall(lowered)
    han = _HAN_CHARS.findall(lowered)
    tokens.extend(han)
    tokens.extend(first + second for first, second in zip(han, han[1:]))
    return tokens


def fake_vector(text: str) -> list[float]:
    """按 token 打点 + L2 归一化的确定性假向量。

    归一化之后余弦相似度就等于点积，共享 token 越多越接近 1；
    空文本返回全零向量（与生产 fallback 的行为一致，不参与相似排序）。
    """
    dimension = settings.rag_embedding_dimension
    vector = [0.0] * dimension

    for token in tokenize(text):
        digest = sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dimension
        vector[index] += 1.0

    norm = sum(value * value for value in vector) ** 0.5
    if norm == 0.0:
        return vector
    return [value / norm for value in vector]


def fake_embed_texts(texts, user_id, db, **kwargs):
    """embed_texts 的替身。吃掉 allow_fallback 等关键字参数。"""
    return [fake_vector(text) for text in (texts or [])]


def fake_embed_query(query, user_id, db, **kwargs):
    """embed_query 的替身。"""
    return fake_vector(query)


# install() 会把被替换前的真实现记在这里，供测降级行为的用例取回。
original_embed_texts = None
original_embed_query = None


def install(monkeypatch) -> None:
    """把所有 embed_texts / embed_query 绑定点换成假实现。

    必须逐个消费模块 patch：它们是 `from ..rag.embedding import embed_texts`
    这样的模块级绑定，只改 app.rag.embedding 上的名字对已经 import 过的
    模块无效。

    同时把原实现记到 original_embed_texts / original_embed_query：测降级
    行为本身的用例需要把真函数 patch 回去（monkeypatch 每个用例结束会
    自动撤销，所以每次 install 拿到的都是原函数，不会积层）。
    """
    global original_embed_texts, original_embed_query

    from app.memory import consolidator as memory_consolidator
    from app.memory import manager as memory_manager
    from app.memory import retriever as memory_retriever
    from app.rag import embedding as rag_embedding
    from app.rag import retriever as rag_retriever
    from app.rag import service as rag_service

    original_embed_texts = rag_embedding.embed_texts
    original_embed_query = rag_embedding.embed_query

    monkeypatch.setattr(rag_embedding, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(rag_embedding, "embed_query", fake_embed_query)
    monkeypatch.setattr(memory_manager, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(memory_consolidator, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(memory_retriever, "embed_query", fake_embed_query)
    monkeypatch.setattr(rag_retriever, "embed_query", fake_embed_query)
    monkeypatch.setattr(rag_service, "embed_texts", fake_embed_texts)

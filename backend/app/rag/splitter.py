from dataclasses import dataclass
from hashlib import sha256


@dataclass(frozen=True)
class TextChunk:
    index: int
    content: str
    token_count: int
    content_hash: str


def split_text(text: str, chunk_size: int, overlap: int) -> list[TextChunk]:
    normalized = "\n".join(line.rstrip() for line in text.splitlines())
    paragraphs = [item.strip() for item in normalized.split("\n\n") if item.strip()]
    units = paragraphs or [normalized]

    chunks: list[str] = []
    current = ""
    for unit in units:
        if len(unit) > chunk_size:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_split_long_text(unit, chunk_size, overlap))
            continue

        candidate = f"{current}\n\n{unit}".strip() if current else unit
        if len(candidate) <= chunk_size:
            current = candidate
        else:
            chunks.append(current)
            current = unit

    if current:
        chunks.append(current)

    return [
        TextChunk(
            index=index,
            content=chunk,
            token_count=_estimate_tokens(chunk),
            content_hash=sha256(chunk.encode("utf-8")).hexdigest(),
        )
        for index, chunk in enumerate(chunks)
        if chunk.strip()
    ]


def _split_long_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    chunks = []
    start = 0
    step = max(1, chunk_size - overlap)
    while start < len(text):
        chunk = text[start : start + chunk_size].strip()
        if chunk:
            chunks.append(chunk)
        start += step
    return chunks


def _estimate_tokens(text: str) -> int:
    # 粗略估算足够用于记录和后续限流，真正 token 由模型调用返回。
    return max(1, len(text) // 4)

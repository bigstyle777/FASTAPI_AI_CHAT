from .retriever import RetrievalHit


def build_context_message(hits: list[RetrievalHit], max_chars: int) -> dict[str, str] | None:
    if not hits:
        return None

    remaining = max_chars
    blocks = []
    for index, hit in enumerate(hits, start=1):
        content = hit.content.strip()
        if not content or remaining <= 0:
            break
        clipped = content[:remaining]
        remaining -= len(clipped)
        blocks.append(
            f"[{index}] source={hit.filename} chunk_id={hit.chunk_id}\n{clipped}"
        )

    if not blocks:
        return None

    context = "\n\n".join(blocks)
    return {
        "role": "system",
        "content": (
            "Use the following retrieved knowledge only when it is relevant. "
            "If it does not answer the user, say so and answer from general knowledge. "
            "Do not invent citations.\n\n"
            f"{context}"
        ),
    }

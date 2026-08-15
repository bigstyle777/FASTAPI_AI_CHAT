import json
import logging

from sqlalchemy.orm import Session

from app.services.llm import _get_client, _get_user_ai_settings
from app.services.task.memory_queue import enqueue_memory_embedding

from ..crud import (
    create_memory,
    delete_memory,
    delete_memory_embeddings,
    get_memories,
    update_memory,
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """
你负责从用户消息中提取值得长期记忆的信息。

只提取未来对帮助用户有价值的信息，例如：
- 长期学习方向
- 长期项目
- 编程技术偏好
- 长期目标
- 用户明确要求记住的信息

不要记录：
- 一次性的任务
- 临时问题
- 普通闲聊
- 密码、API Key 等敏感信息

如果没有值得记忆的信息，返回空数组。

只返回 JSON：
{
    "memories": [
        "..."
    ]
}
"""


def create_memory_service(
    db: Session,
    user_id: int,
    content: str,
):
    content = (content or "").strip()

    if not content:
        raise ValueError("Memory 内容不能为空")

    memory = create_memory(
        db,
        user_id,
        content,
    )

    # 向量化交给 Celery 后台任务；入队失败不阻塞保存，检索时会自动回填
    enqueue_memory_embedding(memory.id, user_id)

    return memory


def get_memories_service(
    db: Session,
    user_id: int,
):
    return get_memories(
        db,
        user_id,
    )


def delete_memory_service(
    db: Session,
    user_id: int,
    memory_id: int,
):
    success = delete_memory(
        db,
        memory_id,
        user_id,
    )

    if not success:
        raise ValueError("Memory 不存在")

    return {
        "success": True,
        "memory_id": memory_id,
    }


def update_memory_service(
    db: Session,
    user_id: int,
    memory_id: int,
    content: str,
):
    content = (content or "").strip()

    if not content:
        raise ValueError("Memory 内容不能为空")

    memory = update_memory(
        db,
        memory_id,
        user_id,
        content,
    )

    if memory is None:
        raise ValueError("Memory 不存在")

    # 内容变了，旧的句子向量作废：先删掉，再交给 Celery 重新向量化
    delete_memory_embeddings(db, memory_id)
    enqueue_memory_embedding(memory_id, user_id)

    return memory


def extract_memory(client, model, message: str) -> list[str]:
    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": f"从以下消息中提取记忆内容：{message}",
            },
        ],
        max_tokens=200,
    )

    content = response.choices[0].message.content.strip()

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return []

    memories = data.get("memories", [])

    if not isinstance(memories, list):
        return []

    return [
        memory.strip()
        for memory in memories
        if isinstance(memory, str) and memory.strip()
    ]


def extract_memory_for_user(
    message: str,
    user_id: int,
    db,
) -> list[str]:
    api_key, provider = _get_user_ai_settings(
        user_id=user_id,
        db=db,
    )

    result = _get_client(
        api_key=api_key,
        provider=provider,
    )

    if not result:
        return []

    client, model = result

    try:
        return extract_memory(
            client=client,
            model=model,
            message=message,
        )
    except Exception:
        return []


def save_memories(
    db,
    user_id: int,
    memories: list[str],
):
    saved = []

    for content in memories:
        memory = create_memory(
            db=db,
            user_id=user_id,
            content=content,
        )
        saved.append(memory)
        enqueue_memory_embedding(memory.id, user_id)

    return saved


def extract_and_save_memory(
    db,
    user_id: int,
    message: str,
):
    memories = extract_memory_for_user(
        message=message,
        user_id=user_id,
        db=db,
    )

    if not memories:
        return []

    return save_memories(
        db=db,
        user_id=user_id,
        memories=memories,
    )


# 读取memory
def build_memory_context(
    db,
    user_id: int,
) -> str:
    memories = get_memories(
        db,
        user_id,
    )

    if not memories:
        return ""

    lines = ["用户长期记忆："]

    for memory in memories:
        lines.append(f"- {memory.content}")

    return "\n".join(lines)

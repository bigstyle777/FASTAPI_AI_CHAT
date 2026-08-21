"""LLM 记忆提取与保存（Celery 后台任务用）。"""

import json
import logging

from .ai_client import get_client, get_user_ai_settings
from .task.memory_queue import enqueue_memory_embedding

from ..crud import create_memory

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
    api_key, provider = get_user_ai_settings(
        user_id=user_id,
        db=db,
    )

    result = get_client(
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

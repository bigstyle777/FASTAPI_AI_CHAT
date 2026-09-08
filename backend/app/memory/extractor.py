"""Memory 提取：一轮对话结束后，用一次 LLM 调用把值得记住的东西结构化。

Message ≠ Memory
----------------
原始消息不直接进库，也不直接拿去做 embedding。必须先过这一层判断：
"这条对话里有什么是下次还用得上的？" 判断不出来就返回空的 memories。
直接把聊天记录 vectorize 存进去，检索回来的只会是一堆闲聊碎片。

只提取本轮
----------
输入由 build_extraction_input 组装：最近几条上下文 + 本轮 user/assistant。
上下文只用来消解指代，渲染时会被标成单独一段（见 build_conversation_text），
prompt 里明确要求只从"本轮"提取。两个理由：
- episodic 一律 CREATE 不幂等，整段历史每轮重提就会堆重复条目；
- 注入的 system 消息里本来就是已有记忆，喂回来会形成"提取自己记忆"
  的自我强化回路（semantic 被反复 upsert、episodic 被重复建档），
  所以上下文里的 system 消息一律剔除。
"""

import json
import logging

from ..core.ai_client import get_client, get_user_ai_settings
from ..core.config import settings
from . import config
from .schemas import ExtractionResult, MemoryAction

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
你负责从一段对话里提取值得长期记住的信息。

记忆分两类：
1. semantic —— 稳定的事实或偏好，用 key / value 表达。
   key 要短且稳定，同一个意思在不同次提取里必须写成同一个 key，
   这样才能覆盖更新而不是堆重复条目。例如：
   {"action": "UPSERT", "type": "semantic", "key": "code_indent",
    "value": "偏好 4 个空格缩进", "importance": 0.6, "confidence": 0.9}
2. episodic —— 过去发生的具体事件或决策，用 summary 表达，写清前因后果。
   例如：
   {"action": "CREATE", "type": "episodic",
    "summary": "决定知识库检索改用 pgvector 而非 Elasticsearch，理由是运维成本",
    "event_type": "decision", "importance": 0.9, "confidence": 0.9}

该记的：
- 用户明确的偏好、习惯、技术栈选择
- 长期目标与在做的项目
- 做出的技术决策及其理由
- 用户要求你记住的内容

不要记：
- 一次性任务、临时问题、普通闲聊
- 密码、API Key、令牌等敏感信息
- 对话里已经过时或被用户推翻的说法

action 取值：
- UPSERT：稳定事实，按 key 覆盖更新（semantic 一律用这个）
- CREATE：新发生的事件（episodic 一律用这个）
- IGNORE：看过但认为不值得记

importance 是"这条对以后有多大用"（0~1），confidence 是"我多大把握没理解错"（0~1）。

输入可能分成两段：标着"此前的上下文"的部分**只用来理解本轮里的指代**
（比如用户说"就用它吧"到底指什么），不要从里面提取任何记忆 ——
那些内容在它们自己发生的那一轮已经提取过了，重复提取只会堆出重复条目。
只从标着"本轮对话"的部分提取。

没有任何值得记的内容时，返回 {"memories": []}。
只返回 JSON，不要任何解释文字或代码块标记。
"""


def _strip_code_fence(text: str) -> str:
    """去掉 LLM 常见的 ```json ... ``` 包裹。"""
    stripped = text.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        if first_newline != -1:
            stripped = stripped[first_newline + 1 :]
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[: -3]
    return stripped.strip()


def parse_extraction(raw: str) -> list[MemoryAction]:
    """把 LLM 输出解析成动作列表；解析不出来就返回空列表，不抛异常。

    提取是后台任务，解析失败不该让任务重试或把异常抛回主流程 ——
    丢掉这一轮的记忆，代价远小于让整个 Celery 任务失败。
    """
    if not raw or not raw.strip():
        return []

    candidate = _strip_code_fence(raw)
    # LLM 偶尔在 JSON 前后加一句说明，只取最外层花括号之间的部分
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end <= start:
        logger.warning("记忆提取结果里找不到 JSON 对象：%r", raw[:200])
        return []

    try:
        data = json.loads(candidate[start : end + 1])
    except json.JSONDecodeError:
        logger.warning("记忆提取结果不是合法 JSON：%r", raw[:200])
        return []

    try:
        result = ExtractionResult.model_validate(data)
    except Exception:
        logger.warning("记忆提取结果不符合约定结构：%r", data, exc_info=True)
        return []

    return result.memories


def build_extraction_input(
    context_messages: list[dict[str, str]],
    *,
    user_text: str,
    assistant_text: str,
    max_context: int | None = None,
) -> list[dict[str, str]]:
    """组装交给提取 LLM 的对话：最近几条上下文 + 本轮 user/assistant。

    约定：返回列表的**最后两条**永远是本轮（user 在前、assistant 在后），
    渲染与 Celery 任务都靠这个约定把本轮单独标出来（focus_last=2）。

    从 context_messages 里剔除三类消息：
      - system：记忆注入 / RAG 上下文，不是对话内容。喂回去会让 LLM
        把已有记忆当成新信息再提取一遍（自我强化回路）；
      - 本轮那条 user 与那条 assistant：已经在返回值末尾单独给出，
        留着会在渲染文本里重复出现（调用方的 history 可能已就地
        append 过本轮 assistant）。

    assistant_text 为空（例如生成被中断）时不补空的 assistant 轮，
    只交上下文 + 本轮 user，此时最后一条是 user —— 调用方拿到的
    focus_last 仍为 2 也无害：渲染只是把最后两条归到"本轮"段。
    """
    limit = (
        config.EXTRACTION_CONTEXT_MESSAGES if max_context is None else max_context
    )
    focus_user = (user_text or "").strip()
    focus_assistant = (assistant_text or "").strip()

    picked: list[dict[str, str]] = []
    skipped_user = False
    skipped_assistant = False
    for item in reversed(context_messages or []):
        role = item.get("role", "user")
        content = (item.get("content") or "").strip()
        if role == "system" or not content:
            continue
        if not skipped_user and role == "user" and content == focus_user:
            skipped_user = True
            continue
        if (
            not skipped_assistant
            and role == "assistant"
            and focus_assistant
            and content == focus_assistant
        ):
            skipped_assistant = True
            continue
        picked.append({"role": role, "content": content})
        if limit > 0 and len(picked) >= limit:
            break
    picked.reverse()

    focus: list[dict[str, str]] = []
    if focus_user:
        focus.append({"role": "user", "content": focus_user})
    if focus_assistant:
        focus.append({"role": "assistant", "content": focus_assistant})
    return [*picked, *focus]


def build_conversation_text(
    messages: list[dict[str, str]],
    *,
    focus_last: int = 0,
) -> str:
    """把对话压成一段给 LLM 看的文本，超长时从尾部截断。

    只保留最近的内容：早期的闲聊对"该记住什么"基本没有价值，
    而会话可能很长，全喂进去既费钱又容易超出上下文。

    focus_last > 0 时把最后这么多条单独标成"本轮对话"，前面的标成
    "此前的上下文"，让 LLM 知道只该从哪一段提取（见 SYSTEM_PROMPT）。
    默认 0 即不分段，保持旧的渲染行为。

    system 消息一律跳过：它们是注入的记忆 / 检索上下文，不是对话内容。
    """
    lines = []
    for item in messages:
        role = item.get("role", "user")
        if role == "system":
            continue
        content = (item.get("content") or "").strip()
        if content:
            speaker = "用户" if role == "user" else "助手"
            lines.append(f"{speaker}：{content}")

    if 0 < focus_last < len(lines):
        split = len(lines) - focus_last
        text = (
            config.EXTRACTION_CONTEXT_HEADER
            + "\n"
            + "\n".join(lines[:split])
            + "\n\n"
            + config.EXTRACTION_FOCUS_HEADER
            + "\n"
            + "\n".join(lines[split:])
        )
    else:
        text = "\n".join(lines)

    limit = settings.memory_extraction_max_chars
    if len(text) > limit:
        text = text[-limit:]
    return text


def extract_memories(
    client,
    model: str,
    conversation: list[dict[str, str]],
    *,
    focus_last: int = 0,
) -> list[MemoryAction]:
    """调一次 LLM 做提取。client / model 由调用方准备好。

    focus_last 透传给 build_conversation_text：由 build_extraction_input
    组装的输入应传 2（本轮 user + assistant）。
    """
    conversation_text = build_conversation_text(conversation, focus_last=focus_last)
    if not conversation_text.strip():
        return []

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"请从下面这段对话中提取记忆：\n\n{conversation_text}",
            },
        ],
        max_tokens=800,
    )

    content = response.choices[0].message.content or ""
    return parse_extraction(content)


def extract_memories_for_user(
    db,
    user_id: int,
    conversation: list[dict[str, str]],
    *,
    focus_last: int = 0,
) -> list[MemoryAction]:
    """按用户的 AI 设置建 client 并提取（Celery 后台任务用这个）。"""
    api_key, provider = get_user_ai_settings(user_id=user_id, db=db)
    result = get_client(api_key=api_key, provider=provider)
    if not result:
        logger.info("用户 %s 没有可用的 AI 配置，跳过记忆提取", user_id)
        return []

    client, model = result
    try:
        return extract_memories(client, model, conversation, focus_last=focus_last)
    except Exception:
        logger.exception("用户 %s 的记忆提取失败", user_id)
        return []

"""验证历史截断：长会话喂给模型的 prompt 能压到多少，边界情况对不对。

跑法：.venv/Scripts/python.exe bench_context_trim.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "backend"))

from app.chat.services.message_context import trim_chat_messages  # noqa: E402
from app.core.config import settings  # noqa: E402


def make_history(count: int, chars_each: int) -> list[dict]:
    """造一段会话历史：用户和助手交替，内容长度固定。"""
    history = []
    for i in range(count):
        role = "user" if i % 2 == 0 else "assistant"
        history.append({"role": role, "content": f"第{i + 1}条：" + "字" * chars_each})
    return history


def show(label: str, history: list[dict]):
    trimmed = trim_chat_messages(history)
    before = sum(len(m["content"]) for m in history)
    after = sum(len(m.get("content") or "") for m in trimmed)
    print(
        f"{label:<26} {len(history):>5} 条 {before:>8} 字符"
        f"   →   {len(trimmed):>3} 条 {after:>7} 字符"
    )
    return trimmed


print("=" * 78)
print("历史截断效果")
print(
    f"预算：最多 {settings.chat_context_max_messages} 条 / "
    f"{settings.chat_context_max_chars} 字符"
)
print("=" * 78)

print("\n【条数上限生效】每条都很短")
short = show("200 条 × 20 字符", make_history(200, 20))

print("\n【字符预算生效】每条都很长，条数没超但字数爆了")
long_chat = show("30 条 × 2000 字符", make_history(30, 2000))

print("\n【两个都很长】")
huge = show("500 条 × 3000 字符", make_history(500, 3000))

print()
print("=" * 78)
print("正确性检查")
print("=" * 78)

history = make_history(200, 20)
trimmed = trim_chat_messages(history)

checks = [
    ("保留的一定是最近的消息", trimmed[0] == history[-len(trimmed)]),
    ("最新那条没有被丢掉", trimmed[-1] == history[-1]),
    (
        "顺序仍是时间正序",
        [m["content"] for m in trimmed]
        == [m["content"] for m in history[-len(trimmed) :]],
    ),
    ("条数不超预算", len(trimmed) <= settings.chat_context_max_messages),
    (
        "字数不超预算（单条超长除外）",
        sum(len(m["content"]) for m in trimmed) <= settings.chat_context_max_chars,
    ),
]

print("\n【边界情况】")
edge_one_huge = [{"role": "user", "content": "字" * 999999}]
edge_empty: list[dict] = []
edge_short = [{"role": "user", "content": "你好"}]

print(f"  单条就超预算      → 保留 {len(trim_chat_messages(edge_one_huge))} 条（不能是 0）")
print(f"  空历史            → 保留 {len(trim_chat_messages(edge_empty))} 条")
print(f"  只有一条短消息    → 保留 {len(trim_chat_messages(edge_short))} 条")
print(f"  内容为空的消息    → 保留 {len(trim_chat_messages([{'role': 'user'}]))} 条")

checks.append(("单条超预算也至少保留 1 条", len(trim_chat_messages(edge_one_huge)) == 1))
checks.append(("空历史不报错", trim_chat_messages(edge_empty) == []))

print()
print("=" * 78)
for label, ok in checks:
    print(f"  {'通过' if ok else '失败'}  {label}")
print("=" * 78)

assert all(ok for _, ok in checks), "有检查项未通过"
print("\n全部通过。")

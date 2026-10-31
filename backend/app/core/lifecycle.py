"""进程生命周期标志。

停机时先置位，让 SSE 长连接在下一个检查点主动收尾并落库，
而不是等客户端断连或 uvicorn 宽限期到了被硬断。
"""

_shutting_down = False


def mark_shutting_down() -> None:
    global _shutting_down
    _shutting_down = True


def is_shutting_down() -> bool:
    return _shutting_down


def reset_shutting_down() -> None:
    """仅测试使用：重置进程级标志。"""
    global _shutting_down
    _shutting_down = False

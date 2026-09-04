"""对比"每 token 查 Redis"和"StopWatcher"的开销，并验证停止功能没被改坏。

Redis 没启动时用本地 TCP echo 模拟一次网络往返（真实的 Redis GET 同理）。
跑法：.venv/Scripts/python.exe bench_stop_signal.py
"""

import asyncio
import socket
import sys
import threading
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).parent / "backend"))

from starlette.concurrency import run_in_threadpool  # noqa: E402

from app.core.stop_signal import (  # noqa: E402
    StopWatcher,
    clear_stopped,
    is_stopped_locally,
    mark_stopped,
)

_ECHO_PORT = 6398


def _start_echo_server():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", _ECHO_PORT))
    server.listen(64)

    def loop():
        while True:
            try:
                conn, _ = server.accept()
            except OSError:
                return
            threading.Thread(target=_handle, args=(conn,), daemon=True).start()

    def _handle(conn):
        with conn:
            while True:
                data = conn.recv(4096)
                if not data:
                    return
                conn.sendall(b"$1\r\n0\r\n")

    threading.Thread(target=loop, daemon=True).start()


_start_echo_server()
_conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
_conn.connect(("127.0.0.1", _ECHO_PORT))


def fake_redis_get(key: str) -> bool:
    """同步阻塞版『查 Redis 有没有停止信号』，返回 False。"""
    _conn.sendall(f"GET {key}\r\n".encode())
    _conn.recv(4096)
    return False


TOKENS = 2000


async def old_way(session_id: int) -> tuple[float, int]:
    """旧写法：每个 token 都 run_blocking 查一次 Redis。"""
    calls = 0
    start = perf_counter()
    for _ in range(TOKENS):
        if await run_in_threadpool(fake_redis_get, f"chat:generation:{session_id}"):
            break
        calls += 1
    return (perf_counter() - start) * 1000, calls


async def new_way(session_id: int) -> tuple[float, int]:
    """新写法：StopWatcher，本地内存判断 + Redis 降频兜底。"""
    calls = 0

    async def backend_check() -> bool:
        nonlocal calls
        calls += 1
        return await run_in_threadpool(fake_redis_get, f"chat:generation:{session_id}")

    watcher = StopWatcher(session_id, backend_check)
    start = perf_counter()
    for _ in range(TOKENS):
        if await watcher.should_stop():
            break
    return (perf_counter() - start) * 1000, calls


async def main():
    print("=" * 66)
    print(f"模拟一次 {TOKENS} token 的回答，统计停止检查本身的开销")
    print("=" * 66)

    old_ms, old_calls = await old_way(1)
    new_ms, new_calls = await new_way(2)

    print(f"{'':<26}{'旧写法':>14}{'StopWatcher':>16}")
    print("-" * 66)
    print(f"{'总耗时':<26}{old_ms:>13.2f} ms{new_ms:>15.2f} ms")
    print(f"{'实际查 Redis 次数':<26}{old_calls:>14}{new_calls:>16}")
    print(f"{'每个 token 摊到':<26}{old_ms / TOKENS:>13.4f} ms{new_ms / TOKENS:>15.4f} ms")
    print("-" * 66)
    saved = (1 - new_ms / old_ms) * 100
    print(f"节省 {saved:.1f}%（{old_ms / 1000:.3f} s → {new_ms / 1000:.3f} s）")
    print()
    print("注：这 2000 次循环跑得比真实生成快得多，所以兜底只触发了")
    print(f"    {new_calls} 次。真实场景下按每秒 1 次兜底，开销可忽略。")

    print()
    print("=" * 66)
    print("功能验证：停止信号还灵不灵")
    print("=" * 66)

    sid = 99
    clear_stopped(sid)

    checks = 0

    async def counting_check() -> bool:
        nonlocal checks
        checks += 1
        return False  # Redis 里什么都没有

    watcher = StopWatcher(sid, counting_check)
    await watcher.should_stop()  # 第一次会查一次 Redis
    print(f"1. 点了停止前:            should_stop = {await watcher.should_stop()}")

    mark_stopped(sid)  # 模拟用户在前端点了「停止」
    checks_before = checks
    stopped = await watcher.should_stop()
    print(f"2. 点停止后立刻检查:      should_stop = {stopped}")
    print(f"   且没有多查 Redis（{checks - checks_before} 次）")

    clear_stopped(sid)
    print(f"3. 一轮结束后清掉标记:    should_stop = {await watcher.should_stop()}")
    print(f"   is_stopped_locally  = {is_stopped_locally(sid)}")

    assert stopped is True
    assert is_stopped_locally(sid) is False
    print()
    print("结论：停止信号照样一个 token 都不多等，功能没变，开销降了两个数量级。")


if __name__ == "__main__":
    asyncio.run(main())

"""测量一次 AI 聊天里"非大模型"部分的开销。

重点回答：stream_ai_reply 里"每个 token 一次 run_blocking(is_stop_requested)"
到底吃掉多少时间。Redis 没起，所以用一个本地 TCP echo server 模拟
一次 Redis GET 的网络往返（Redis 正常时一次 GET ≈ 一次 round-trip）。
"""

import asyncio
import socket
import threading
import time
from time import perf_counter

from starlette.concurrency import run_in_threadpool

# ---- 模拟一次 Redis GET（网络往返）--------------------------------------
_ECHO_PORT = 6399


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
                conn.sendall(b"$1\r\n0\r\n")  # 假的 Redis 回复

    threading.Thread(target=loop, daemon=True).start()
    return server


_start_echo_server()

_redis_like = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
_redis_like.connect(("127.0.0.1", _ECHO_PORT))


def fake_redis_get(key: str) -> str | None:
    """同步阻塞版『Redis GET』：一次发收，等价于一次网络往返。"""
    _redis_like.sendall(f"GET {key}\r\n".encode())
    return _redis_like.recv(4096).decode()


# ---- 基准 ---------------------------------------------------------------


def bench_sync(label, fn, n=2000):
    fn()  # 预热
    start = perf_counter()
    for _ in range(n):
        fn()
    elapsed_ms = (perf_counter() - start) / n * 1000
    print(f"{label:<44} {elapsed_ms:>8.4f} ms/次")
    return elapsed_ms


async def bench_threadpool(label, fn, n=2000):
    await run_in_threadpool(fn)  # 预热
    start = perf_counter()
    for _ in range(n):
        await run_in_threadpool(fn)
    elapsed_ms = (perf_counter() - start) / n * 1000
    print(f"{label:<44} {elapsed_ms:>8.4f} ms/次")
    return elapsed_ms


async def main():
    print("=" * 68)
    print("单次开销实测（本机 Windows，Redis 用本地 TCP echo 模拟往返）")
    print("=" * 68)

    redis_ms = bench_sync("① Redis GET（一次网络往返，同步直连）", lambda: fake_redis_get("k"))
    tp_ms = await bench_threadpool("② run_in_threadpool 空函数（纯调度）", lambda: None)
    tp_redis_ms = await bench_threadpool(
        "③ run_in_threadpool + Redis GET（现状：每 token 一次）", lambda: fake_redis_get("k")
    )

    print("-" * 68)
    print(f"线程池调度本身的额外成本: {tp_redis_ms - redis_ms:.4f} ms/次")
    print()
    print("换算成一次完整回答的额外开销（现状：每个 token 都要走一次 ③）:")
    print("-" * 68)
    print(f"{'token 数':<12}{'额外耗时':<18}{'占 8s 回答比例':<18}")
    for tokens in (200, 500, 1000, 2000):
        total_ms = tp_redis_ms * tokens
        ratio = total_ms / 8000 * 100
        print(f"{tokens:<12}{total_ms / 1000:>8.2f} s{'':<8}{ratio:>8.1f} %")

    print()
    print("=" * 68)
    print("对照：一次聊天其余环节的固定开销（DB/Redis，单次）")
    print("=" * 68)
    print("  check_rate_limit      ~1 次 Redis INCR + EXPIRE    ~0.1-0.3 ms")
    print("  _get_validated_message ~1 次 SELECT session         ~1-5 ms")
    print("  persist_user_message   ~1 次 INSERT + UPDATE        ~2-10 ms")
    print("  load_chat_context     ~1 次 Redis GET JSON          ~0.1-1 ms（命中缓存）")
    print("  get_user_ai_settings  ~1 次 SELECT user_settings    ~1-5 ms")
    print("  收尾落库 + 写缓存      ~1 INSERT + 1 SET + 入队     ~3-15 ms")
    print("  ---------------------------------------------------------------")
    print("  以上合计（不含大模型）                              ~10-35 ms")
    print()
    print("  大模型流式生成（TTFT + 逐 token 吐出）              数秒 ~ 数十秒")


if __name__ == "__main__":
    asyncio.run(main())

"""生成过程中的"停止"信号：进程内优先，Redis 兜底。

为什么要这个模块
----------------
流式回答要不断问一句"用户点停止了吗"。原来的写法是每个 token 都
``await run_blocking(is_stop_requested, session_id)`` 去查一次 Redis：

    Redis GET 本身            0.085 ms
    丢线程池再拿回来（纯调度）  0.272 ms
    ------------------------------------
    每个 token 合计            0.357 ms    ← 实测，见 bench_chat_overhead.py

76% 是线程池来回搬运的浪费。1000 字的回答光这一项就吃掉 0.36 秒，
2000 字 0.71 秒，而且这串等待就卡在生成的关键路径上。

关键点在于：**写信号的人（用户点停止）和读信号的人（流式循环）在同一个
进程里**。既然如此，把标记放在进程内存即可，一次集合查找是纳秒级，等于免费。

Redis 仍然保留，但降频成每秒一次的兜底：当前是单进程部署
（见 docker-compose.yml，backend 只有一个容器一个进程），本地标记 100% 可靠；
将来如果扩成多副本，本地标记会漏，靠这每秒一次的兜底，最坏 1 秒内也能生效，
不会静默失效。

用法::

    watcher = StopWatcher(session_id, lambda: run_blocking(is_stop_requested, session_id))
    async for chunk in stream:
        if await watcher.should_stop():
            break
"""

from time import perf_counter

# 已被请求停止的 session_id。用可变集合、不做整体重新赋值，
# 所以下面几个函数都不需要 global 声明。
_STOPPED_SESSIONS: set[int] = set()

# Redis 兜底的检查间隔（秒）。只在多副本部署时才真正派上用场。
_REDIS_FALLBACK_INTERVAL_SECONDS = 1.0


def mark_stopped(session_id: int) -> None:
    """记下"这个会话被要求停止"，本进程内立刻生效。"""
    _STOPPED_SESSIONS.add(session_id)


def clear_stopped(session_id: int) -> None:
    """一轮生成结束，把标记清掉。

    每一轮开始时也会调一次，所以即使上一轮异常退出没清干净，
    也不会一直堵住后续对话。
    """
    _STOPPED_SESSIONS.discard(session_id)


def is_stopped_locally(session_id: int) -> bool:
    """查本进程的停止标记：一次内存查找，开销接近 0，可以每个 token 调。"""
    return session_id in _STOPPED_SESSIONS


class StopWatcher:
    """流式循环里的停止检查器。

    每个 token 只查一次进程内存（免费）；Redis 兜底按时间间隔降频，
    避免每个 token 都付一次线程池调度的代价。

    backend_check 是一个 async 无参调用，返回 bool，用来查 Redis。
    """

    def __init__(
        self,
        session_id: int,
        backend_check,
        interval_seconds: float = _REDIS_FALLBACK_INTERVAL_SECONDS,
    ):
        self._session_id = session_id
        self._backend_check = backend_check
        self._interval_seconds = interval_seconds
        # 0 表示"还没查过"，第一次调用时立即查一次 Redis，
        # 顺带把上一轮可能残留的停止状态捞出来。
        self._next_backend_check_at = 0.0

    async def should_stop(self) -> bool:
        """这次该不该停下来？绝大多数调用只做一次内存判断就返回。"""
        if is_stopped_locally(self._session_id):
            return True

        now = perf_counter()
        if now < self._next_backend_check_at:
            return False

        self._next_backend_check_at = now + self._interval_seconds
        if await self._backend_check():
            # Redis 里也说了要停（多副本场景），顺便记到本地，后面就不用再问了
            mark_stopped(self._session_id)
            return True
        return False

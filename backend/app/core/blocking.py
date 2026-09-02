"""异步链路里调用同步阻塞代码的统一入口。

为什么要这个模块
----------------
本项目的数据层是**同步**的：

- SQLAlchemy 用的是 ``postgresql+psycopg`` 同步驱动（见 core/config.py）
- Redis 用的是同步的 ``redis.Redis``（见 core/redis.py）

它们发起网络请求时会一直占着调用线程，干等回包。HTTP 链路改成 async 之后，
如果直接在事件循环上调用 ``db.query(...)`` 或者 ``is_stop_requested(...)``，
**一次慢查询就会冻住所有并发请求** —— 这比原来放在线程池里跑要糟得多。

所以在 async 代码里碰到同步的 DB / Redis 调用，一律走 ``run_blocking`` 丢回
线程池：事件循环继续处理别的请求，只有一个线程在那儿等网络。

为什么不干脆换成 asyncpg / redis.asyncio？
    那要把 ``db.query(X).filter(...)`` 全部改写成
    ``await db.execute(select(X).where(...))``，改动面覆盖
    admin / user / chat / rag / memory / task 六个域。而这些查询本身只有
    几毫秒，占一次对话耗时的极小部分；真正占时间（95% 以上）的是等大模型
    回包。把力气花在后者上，收益大得多。

用法::

    session = await run_blocking(get_session_by_user, db, session_id, user_id)
"""

from typing import Callable, TypeVar

from starlette.concurrency import run_in_threadpool

T = TypeVar("T")


async def run_blocking(func: Callable[..., T], *args, **kwargs) -> T:
    """在线程池里执行一个同步阻塞函数，不占住事件循环。"""
    return await run_in_threadpool(func, *args, **kwargs)

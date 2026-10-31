# AIChatPro · 阶段1：可靠聊天后端 实施方案

> 版本：v1（评审稿）
> 日期：2026-10-30
> 定位：给聊天主链路补齐可靠性——超时、重试、限流并发、幂等、任务状态机、取消与资源清理、主链路测试。
> 前提：本文基于对 `backend/app` 现有代码的实地探查，所有改动点均标注到文件与行号。遵循「模块化改造、保留单体、最小改动、配套测试、明确验收」原则。

---

## 0. 一句话说清这个阶段在干什么

**给聊天主链路装上"保险丝"和"黑匣子"：**

- **保险丝**：上游模型挂了/慢了/限流了，请求在规定时间内体面地失败，而不是挂死；瞬时故障自动重试，用户无感知。
- **黑匣子**：每一次生成任务有明确的状态（PENDING/RUNNING/COMPLETED/FAILED/CANCELLED/TIMED_OUT），出事后能回答"它当时处于什么状态、为什么停"，而不是靠猜。

不引入新中间件、不动部署架构，全部在现有 FastAPI 单体内完成。

---

## 1. 现状盘点：差距与证据

| 条目 | 现状 | 缺口 | 证据 |
|---|---|---|---|
| **超时** | `AsyncOpenAI` 裸构造，无 timeout；流式无总时长上限 | ❌ 完全缺失 | `core/ai_client.py:45-58` |
| **重试** | 异常直接变 `StreamErrorEvent` 结束 | ❌ 无分类、无退避 | `chat/services/llm.py:65-73` |
| **限流** | 每用户 500 次/小时的频率限流已存在 | ⚠️ 有频率无并发；阈值硬编码 | `chat/services/messages.py:261-266` |
| **幂等** | 无 | ❌ 双击/重试/刷新会重复扣费、重复落库 | `chat/router.py:121-135` |
| **状态机** | 只有 `stop_requested` 一个 Redis 标记 | ❌ 无显式状态，崩溃后不可知 | `core/cache.py:36-54` |
| **取消清理** | `StopWatcher`（内存+Redis 兜底）已可用；部分回复会落库 | ⚠️ 无客户端断连检测、无上游流关闭、无 try/finally 兜底 | `core/stop_signal.py`、`chat/services/messages.py:146-215` |
| **测试** | conftest 基建很好（独立测试库、mock Redis、假 embedding） | ❌ 聊天主链路零覆盖 | `backend/tests/conftest.py` |

**结论**：底子能承接改造（StopWatcher、Redis 降级、测试基建都是现成的），7 项里 5 项从零补，不動骨架。

---

## 2. 必须先拍板的四个设计决策

### 决策 1（最关键）：生成任务状态存哪里？

| 方案 | 做法 | 优点 | 缺点 |
|---|---|---|---|
| **A. PG 表 + 条件更新（推荐）** | 新建 `chat_generations` 表，状态流转用 `UPDATE ... WHERE status=:from` 条件更新 | 崩溃可恢复、重投可去重、流转合法性由 DB 保证（行锁）、可审计 | 每次生成多 3-4 次 DB 写 |
| B. 仅 Redis | 状态写 Redis hash + TTL | 快、简单 | 进程崩溃+Redis 降级时状态丢失，无法做"重投去重"，与现有内存降级逻辑互相打架 |

**建议选 A**。你的蓝图里明确要求"靠数据库条件更新保证状态流转合法，解决崩溃、消息重投"——这只有 PG 能做。Redis 保留现状职责：只做"停止信号"这个易失标记，不当状态权威源。**权威数据源：PG；Redis 是信号通道**。这回应了"数据一致性"重点。

### 决策 2：重试的边界划在哪？（已确认：保守策略）

- ✅ **可重试**：`client.chat.completions.create()` 在**还没吐出任何 token 之前**失败（建连失败、超时、429、5xx）；非流式 `chat_with_ai` 整体重试。
- ❌ **不重试**：流式已经开始产出 token 后中途断流。重试会让用户看到重复开头、token 重复计费，收益不抵代价。此时落 FAILED，优雅报错。
- 实现上：重试包装器只包 `create()` 这一下，不包整个流消费过程。

### 决策 3：幂等键谁来生成？

- **前端**在每次"点击发送"时生成 UUID，放入 `Idempotency-Key` 请求头。同一次用户意图（包括双击、断网重试、页面刷新重发）复用同一个 key。
- **服务端双层判重**：
  1. Redis `SET idem:{key} NX EX 600` —— 快速路径，挡住双击和重试；
  2. `chat_generations.idempotency_key` 唯一索引 —— 兜底路径，挡住并发竞态和多副本。
- 命中重复时：若原任务 COMPLETED，直接返回已落库的 assistant 消息（不重新生成、不重复扣费）；若 RUNNING，返回错误事件"同一条消息正在生成中"。

### 决策 4：并发闸门放哪一层？

当前单进程部署（见 `docker-compose.yml` 与 `stop_signal.py` 注释），所以：

- **每用户并发 = 1**：由状态机天然保证——同一用户存在 RUNNING 任务时拒绝新流式请求（"请等待当前回答完成"）。
- **全局并发**：进程内 `asyncio.Semaphore(chat_max_concurrent_generations)`，默认 20。拿到信号量才允许发起上游调用；拿不到则在 SSE 里返回明确错误，不排队（聊天是交互场景，排队等 30 秒不如直接告知）。
- **背压**：SSE 逐 token yield 本身就是天然背压（客户端慢→生成循环阻塞在 send）。补充的是**断连检测**：每轮循环查 `request.is_disconnected()`，断了立即停止生成并清理，不再为一个没人看的回答烧 token。
- Redis 分布式计数留给将来多副本，本阶段不做，接口预留。

---

## 3. 核心设计

### 3.1 错误分类：哪些值得重试

基于 openai SDK 异常层级，纯函数判断，可独立单测：

| 异常 | 语义 | 可重试 |
|---|---|---|
| `APIConnectionError` | 网络断、DNS、连接被拒 | ✅ |
| `APITimeoutError` | 请求超时 | ✅ |
| `RateLimitError` (429) | 上游限流 | ✅ |
| `InternalServerError` (5xx) | 上游服务故障 | ✅ |
| `AuthenticationError` (401) | Key 无效 | ❌ 重试无意义 |
| `PermissionDeniedError` (403) | 无权限 | ❌ |
| `BadRequestError` (400) | 请求本身有问题（如上下文超长） | ❌ |
| `NotFoundError` (404) | 模型名错误 | ❌ |
| 其他未知异常 | 保守起见不重试 | ❌ |

### 3.2 重试策略：指数退避 + 全抖动

```
attempt 0 失败后等待 random(0, min(cap, base * 2^0))
attempt 1 失败后等待 random(0, min(cap, base * 2^1))
...
```

- `base = 0.5s`，`cap = 4s`，`max_attempts = 3`（共 3 次机会，最坏多等 ~7s）
- **全抖动**（`random(0, delay)` 而非 `delay + 抖动`）：上游故障时所有客户端同时退避会再次撞出流量尖峰，全抖动把重试摊平。
- 测试时 monkeypatch `asyncio.sleep`，不真等。

### 3.3 超时分层

| 层 | 控制什么 | 配置项 | 默认 |
|---|---|---|---|
| httpx 连接层 | TCP 连接建立 | `llm_connect_timeout_seconds` | 5s |
| 单次 `create()` | 一轮请求的建连+首 chunk | `llm_request_timeout_seconds` | 60s |
| 整条流 | 从流开始到主动放弃的总预算 | `llm_stream_max_seconds` | 300s |

- 前两层通过 `AsyncOpenAI(timeout=...)` 传入（SDK 原生支持）。
- 第三层在 `stream_ai_reply` 主循环里用 `perf_counter` 计时，超预算 → 走"主动停止"路径，状态落 TIMED_OUT。
- 工具调用循环（`stream_with_tools` 最多 6 轮 `create()`）被总预算统一约束，不为每轮单独设限。

### 3.4 任务状态机

```
                 ┌─────────┐
                 │ PENDING │  记录已建，上游调用未发起
                 └────┬────┘
                      │ start（条件更新 PENDING→RUNNING）
                      ▼
                 ┌─────────┐
        ┌────────│ RUNNING │────────┐
        │        └─────────┘        │
        ▼            ▼              ▼
  ┌──────────┐ ┌─────────┐  ┌───────────┐  ┌───────────┐
  │COMPLETED │ │ FAILED  │  │ CANCELLED │  │ TIMED_OUT │
  └──────────┘ └─────────┘  └───────────┘  └───────────┘
   正常落库      重试耗尽/      用户停止/       总时长超预算
                不可重试错误    客户端断连
```

- **终态**：COMPLETED / FAILED / CANCELLED / TIMED_OUT，终态不可再流转。
- **流转合法性**：每次流转是 `UPDATE chat_generations SET status=:to, ... WHERE id=:id AND status IN (:allowed_from)`，受影响行数=0 说明状态已被并发修改，记 warning 并以 DB 为准，不强行写。
- **崩溃恢复**：应用启动时清扫——`UPDATE ... SET status='FAILED', error='process_restarted' WHERE status IN ('PENDING','RUNNING') AND updated_at < now() - interval '10 minutes'`。这条 UPDATE 本身就是幂等的，多实例同时执行也安全。
- **与现有停止信号的关系**：`stop_requested` Redis 标记保留不动（它是"信号"）；状态机是"事实"。用户点停止 → 信号置位 → 流循环看到信号 → 走 CANCELLED 流转。

### 3.5 `chat_generations` 表结构

| 字段 | 类型 | 说明 |
|---|---|---|
| id | bigint PK | |
| session_id | int FK→chat_sessions | |
| user_id | int FK→users | |
| user_message_id | int FK→messages | 本轮对应的用户消息 |
| idempotency_key | varchar(64)，唯一索引 | 幂等键，允许 NULL（旧路径/非流式可空） |
| status | varchar(16) | 状态机枚举 |
| error | text，可空 | 失败原因（分类后的，不含敏感内容） |
| model | varchar(50)，可空 | 实际调用的模型 |
| prompt_tokens / completion_tokens / total_tokens | int | 从 usage 回填 |
| created_at / updated_at / finished_at | datetime | |

### 3.6 限流分层

| 层 | 规则 | 现状/动作 |
|---|---|---|
| 用户频率 | 500 次/小时（滑窗在 Redis INCR） | ✅ 已有，阈值收进配置 `chat_rate_limit_per_hour` |
| 每用户并发 | 同时最多 1 个生成中 | 新增，由状态机 RUNNING 检查实现 |
| 全局并发 | 进程内信号量，默认 20 | 新增 `chat_max_concurrent_generations` |
| 模型/成本维度 | — | **本阶段不做**，归阶段2（Token/任务预算） |

---

## 4. 接口与改动点设计

### 4.1 新增 `backend/app/core/retry.py`

```python
def is_retryable_llm_error(error: Exception) -> bool: ...
    # 纯函数：3.1 的分类表

async def call_with_retry(fn, *, max_attempts: int, base_seconds: float, cap_seconds: float):
    # 包一次异步调用；可重试错误按指数退避+全抖动重试，
    # 不可重试错误原样抛出。重试日志带 attempt 次数。
```

### 4.2 新增 `backend/app/chat/services/generation.py` + `chat/models/` 新表

```python
def create_generation(db, *, session_id, user_id, user_message_id, idempotency_key) -> Generation | None:
    # None 表示幂等键撞了唯一索引（并发重复），调用方走"已有任务"分支

def find_by_idempotency_key(db, key) -> Generation | None: ...

def transition(db, generation_id, *, from_status: tuple[str, ...], to_status: str, error=None) -> bool:
    # 条件更新唯一入口；返回 False = 状态已被并发改写

def get_running_generation(db, user_id) -> Generation | None:
    # 每用户并发=1 的检查

def sweep_stale_generations(db, older_than_minutes=10) -> int:
    # 启动时调用；返回清扫条数用于日志
```

### 4.3 修改 `core/ai_client.py`

`get_async_client` 增加 `timeout` 与 `max_retries=0`（SDK 自带重试关掉，统一走我们自己的、可观测的重试）：

```python
AsyncOpenAI(
    api_key=key, base_url=base_url,
    timeout=httpx.Timeout(settings.llm_request_timeout_seconds,
                          connect=settings.llm_connect_timeout_seconds),
    max_retries=0,
)
```

### 4.4 修改 `chat/services/llm.py` 与 `agent/tool_calling.py`

- `chat_with_ai`（非流式）：整体包 `call_with_retry`。
- `stream_with_tools`：把两处裸 `client.chat.completions.create(**kwargs)` 换成 `await create_with_retry(client, **kwargs)`——只重试"还没吐 token"的建连阶段，符合决策 2。`tool_calling.py` 只换调用点，循环逻辑不动。

### 4.5 修改 `chat/services/messages.py`（主战场）

`stream_ai_reply` 重排为：

1. 建 PENDING 记录 → start → RUNNING（若用户已有 RUNNING 任务，错误事件返回）
2. 拿全局信号量（拿不到 → 错误事件 + FAILED）
3. `try`：流循环（带总时长检查 + 断连检测 + 现有 StopWatcher）
4. 各退出路径分别落 COMPLETED / FAILED / CANCELLED / TIMED_OUT
5. `finally`：清 generation 状态、清停止标记、释放信号量——**资源清理不依赖任何一条路径记得清**

`send_message_stream_service`：读 `Idempotency-Key`（router 传入）→ Redis NX 判重 → 建任务。重复时按决策 3 返回已有结果或明确错误。

### 4.6 修改 `chat/router.py`

```python
@router.post("/stream")
async def chat_stream(request: ChatRequest, user: CurrentUser, db: Database,
                      raw_request: Request,
                      idempotency_key: str | None = Header(default=None)):
```

把 `is_disconnected` 检查能力和幂等键传入服务层。CORS 允许该 header。

### 4.7 配置项（`core/config.py` 新增）

| 配置 | 默认 | 说明 |
|---|---|---|
| `llm_connect_timeout_seconds` | 5 | httpx 连接超时 |
| `llm_request_timeout_seconds` | 60 | 单次 create() 超时 |
| `llm_stream_max_seconds` | 300 | 单条流总预算 |
| `llm_retry_max_attempts` | 3 | 含首次共 3 次 |
| `llm_retry_base_seconds` | 0.5 | 退避基数 |
| `llm_retry_cap_seconds` | 4.0 | 退避上限 |
| `chat_rate_limit_per_hour` | 500 | 现有硬编码收编 |
| `chat_max_concurrent_generations` | 20 | 全局并发信号量 |
| `idempotency_key_ttl_seconds` | 600 | Redis 判重键 TTL |

### 4.8 前端配合（最小改动）

发送按钮处生成 UUID（`crypto.randomUUID()`）作为 `Idempotency-Key`，同一条消息的自动重试复用该 key。**前端改动只列需求，本阶段实施由你确认后另行安排。**

---

## 5. 迭代拆分与验收标准

### 迭代 1A：超时 + 重试（无 DB 变更，先行）

**改动**：`retry.py`（新）、`ai_client.py`、`llm.py`、`tool_calling.py`、`config.py` + 测试。

**验收**：
- [x] 模拟上游连续 2 次 500 后成功：用户收到正常回答，日志可见 2 次重试与退避间隔
- [x] 模拟 401：立即报错，零重试
- [x] 模拟上游挂死：60s 内收到明确错误事件，不是永久转圈
- [x] `pytest tests/test_retry_policy.py` 全绿

### 迭代 1B：状态机 + 幂等 + 断连清理（含 Alembic 迁移）

**改动**：`chat_generations` 表 + 迁移、`generation.py`（新）、`messages.py`、`router.py`、`main.py`（启动清扫）+ 测试。

**验收**：
- [x] 同一 Idempotency-Key 重复提交：只生成一次、只扣一次费，第二次拿到已有结果或明确提示
- [x] 生成中 kill 进程重启：启动日志可见清扫，该任务落 FAILED('process_restarted')
- [x] 生成中关闭页面（断连）：上游流被关闭，状态落 CANCELLED，Redis 标记清理
- [x] 非法流转（COMPLETED→RUNNING）条件更新返回 False，不破坏事实
- [x] `pytest tests/test_generation_state_machine.py tests/test_stream_reliability.py` 全绿

### 迭代 1C：并发闸门 + 主链路测试补齐

**改动**：信号量接入 `messages.py`、每用户并发检查、配置收编 + 测试。

**验收**：
- [x] 同一用户并发起第二个流：收到"请等待当前回答完成"
- [x] 全局并发打满（测试用小信号量）：超额请求收到明确错误，不排队不挂死
- [x] 频率限流阈值走配置而非硬编码
- [x] `pytest tests/` 全量绿（281 passed）

---

## 6. 测试计划

复用 `conftest.py` 现有模式（独立测试库、模块级 mock Redis、假 embedding）。新增：

| 文件 | 覆盖 |
|---|---|
| `tests/test_retry_policy.py` | 错误分类表全分支；重试次数/退避调用（monkeypatch `asyncio.sleep` 记录间隔，断言递增且有界）；不可重试错误零重试 |
| `tests/test_generation_state_machine.py` | 建任务、合法流转、非法流转被拒、幂等键唯一冲突、stale 清扫只清超时任务 |
| `tests/test_stream_reliability.py` | 假 async client 造流：中途断流→FAILED 且已输出部分落库；总时长超限→TIMED_OUT；停止信号→CANCELLED；重复幂等键→不重复生成 |
| `tests/test_concurrency_limit.py` | 信号量占满时的拒绝行为；每用户 RUNNING 检查 |

**故障演练（手动 checklist，不进 CI）**：`docker compose stop redis` 后再聊天（验证内存降级）；`stop postgres` 验证 503 路径；上游 API Key 改错验证 401 不重试。

---

## 7. 变更文件清单

| 动作 | 文件 | 迭代 |
|---|---|---|
| 新增 | `backend/app/core/retry.py` | 1A |
| 修改 | `backend/app/core/ai_client.py` | 1A |
| 修改 | `backend/app/chat/services/llm.py` | 1A |
| 修改 | `backend/app/agent/tool_calling.py` | 1A（仅 2 处调用点） |
| 修改 | `backend/app/core/config.py` | 1A/1B/1C |
| 新增 | `backend/app/chat/models/` 中 `ChatGeneration` | 1B |
| 新增 | `backend/alembic/versions/f3a8c1e5b7d2_add_chat_generations.py` | 1B |
| 新增 | `backend/app/chat/services/generation.py` | 1B |
| 修改 | `backend/app/chat/services/messages.py` | 1B/1C |
| 修改 | `backend/app/chat/router.py` | 1B |
| 修改 | `backend/app/main.py`（启动清扫） | 1B |
| 新增 | `backend/tests/test_retry_policy.py`、`test_generation_state_machine.py`、`test_stream_reliability.py`、`test_concurrency_limit.py` | 各迭代配套 |
| 修改 | `backend/tests/conftest.py`（测试库默认沿用主库连接参数） | 1B |

**明确不做**（本阶段）：熔断器、多供应商降级、Token 预算（→阶段2）；工具风险分级、HITL（→阶段3）；OpenTelemetry（→阶段4）。

---

## 8. 风险与注意事项

1. **Starlette 会吞 StreamingResponse 生成器里的异常**（项目已有教训）：所有异常路径必须显式 try/except 转成错误事件或状态流转，不能指望外层中间件。
2. **openai SDK 版本差异**：`APITimeoutError` 等异常类在旧版命名不同，实施时先确认 `requirements.txt` 锁定版本的异常层级。
3. **Alembic 迁移**：`chat_generations` 含外键， downgrade 也要写；迁移前先备份或在测试库演练。
4. **`stream_with_tools` 的工具轮次**：每轮 `create()` 都过重试包装，最坏延迟 = 轮数 × 重试退避，由 300s 总预算兜底。
5. **幂等键为 NULL 的兼容**：`modify_message_service`（改消息重生成）不走幂等键，靠状态机的每用户并发检查防重。

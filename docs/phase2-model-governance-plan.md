# 阶段 2：模型调用治理实施方案

> 阶段 1 解决的是"一次请求怎么可靠地跑完"，阶段 2 解决的是"上游整体不健康时，系统怎么保护自己也保护钱包"。
> 范围：熔断、备用模型降级、Token 预算与成本账本。工具风险分级、提示注入防护归阶段 3；全链路追踪归阶段 4。

---

## 1. 现状盘点

| 能力 | 现状 | 差距 |
|---|---|---|
| 熔断 | 只有 [retry.py](../../backend/app/core/retry.py) 的分类重试：失败后最多再试 `llm_retry_max_attempts-1` 次 | 上游连续 5xx 时每个请求都硬扛 3 次重试 + 退避，反而放大压力；没有"整体停手一段时间"的机制 |
| 备用模型 | [ai_client.py](../../backend/app/core/ai_client.py#L31-L40) 的 `_resolve_provider` 只解析**一个** provider，返回单个 `(client, model)` | 主 provider 故障时没有第二选择；[llm.py](../../backend/app/chat/services/llm.py#L10-L25) 的 `_build_fallback_reply` 只是返回一句文案 |
| Token 预算 | [config.py](../../backend/app/core/config.py#L41-L50) 只约束上下文条数/字符数（输入侧裁剪） | 没有单用户日额度、没有全局成本上限、没有 `max_tokens` 显式约束输出 |
| 成本账本 | usage 只落在 `messages` / `chat_generations` / `agent_runs` 的 token 列 | 没有按模型单价折算的金额，无法回答"今天花了多少、谁花的多" |
| 调用可观测 | 只有重试的 warning 日志 | 没有 provider / 模型 / 耗时 / 重试次数 / 错误类型 / 成本的结构化记录 |

现有可复用的地基：`is_retryable_llm_error`（错误分类，[retry.py](../../backend/app/core/retry.py)）、Redis 失败降级内存（[redis.py](../../backend/app/core/redis.py)）、`check_rate_limit` 的原子 INCR、`chat_generations` 与 `agent_runs` 的 token 字段。

---

## 2. 设计决策

### 决策 1：熔断状态写 Redis，读失败回退进程内存

- 维度：`(provider, base_url)`，不含 model——同 provider 的不同模型通常挂在同一批机器上，按 provider 熔断更不容易误伤。
- 状态机：`CLOSED`（正常）→ 连续失败满 `llm_circuit_failure_threshold`（默认 5）→ `OPEN`（直接拒绝，不再发起请求）→ 冷却 `llm_circuit_cooldown_seconds`（默认 60s）→ `HALF_OPEN`（只放 1 个探测请求）→ 成功即 `CLOSED`，失败回 `OPEN`。
- **只统计可重试类错误**（复用 `is_retryable_llm_error`）：401/400/上下文超长是"我们的问题"，不该把上游判死。
- 存储键 `llm:circuit:{provider}:{host}`，值用 JSON（`failures` / `opened_at`）。Redis 不可用时退化到进程内 dict——单进程部署下语义不变，只是失去多进程共享。
- 与重试的关系：每次重试尝试前检查熔断；若本轮失败刚好把熔断打开，**立即停止剩余重试**，不再硬扛退避。

### 决策 2：降级链按"Key 归属"分两种边界

`get_async_client` 从"返回一个 client"改为"返回目标列表"（主目标 + 可选备用目标），调用方按顺序尝试：

| 场景 | 允许的降级 |
|---|---|
| 用户自带 Key（`UserSetting.api_key` 有值） | 只允许**同 provider 换模型**（如 `deepseek-v4-flash` → `deepseek-chat`），绝不跨供应商 |
| 使用系统 Key（`.env` 兜底） | 允许**跨 provider**（主 deepseek → 备 openai），但仍受各自熔断约束 |

理由：用户自己的 Key 打到另一家供应商会变成"拿用户的 Key 调另一家"的串账，且用户没同意；系统 Key 是我们自己的成本，内部调度自由。

不触发降级的错误：401（Key 无效）、400（请求不合法/超长）、用户主动停止、已经吐出 token 后的断流（与阶段 1 的重试边界一致）。

### 决策 3：预算"事后记账 + 事前闸门"，额度的权威是 Redis 计数、对账的权威是 PG

- 请求前：读当日计数（`budget:user:{id}:{yyyymmdd}` 的 token 数、`budget:global:{yyyymmdd}` 的金额），超限直接 SSE error 拒绝。
- 请求后：按实际 `usage` 写一条 `llm_usage_logs`，并对 Redis 计数做 `INCRBY`。先落库、再累加计数，计数偏少只会多放行一个请求，不会少扣。
- 默认 `0` 表示不限制，保证现网行为不变；只在新配置显式开启后才生效。

### 决策 4：金额用整数微元，价格表随代码发布

- 新增 `app/core/model_pricing.py`：`model -> (输入价, 输出价) / 1K tokens`，按前缀最长匹配（`deepseek-v4-flash` 能命中 `deepseek-` 规则）。未知模型记 `cost_micros=0` 并打 `unknown` 标记，绝不因缺价目表而丢账。
- 金额以"微元（1e-6 元）"存整数，避免浮点累加误差；展示时再除 1e6。
- 价格表放代码而非 DB：改价跟着发布走，可 review，不引入"运行中改价导致历史账目口径不一致"。

### 决策 5：预算缺口落在"生成前"，不做中途掐断

流式生成已经开始吐字后再因为额度不足中断，用户体验是"话说一半被吞"，且 token 已经花了。所以预算检查只放在入口；单次请求的输出上限由 `llm_max_output_tokens`（`max_tokens`）约束，防止单条回答失控。

---

## 3. 错误与降级矩阵

| 错误 | 计熔断 | 重试 | 降级/切换 | 用户看到 |
|---|---|---|---|---|
| 建连失败 / 超时 | ✅ | ✅ 退避重试 | 耗尽后可切备用目标 | 成功则无感；全失败 → "服务暂时不可用" |
| 429 限流 | ✅ | ✅ | 耗尽后可切备用 | 同上 |
| 5xx | ✅ | ✅ | 耗尽后可切备用 | 同上 |
| 401 / 403 | ❌ | ❌ | ❌ | "API Key 无效，请在个人中心检查" |
| 400 / 上下文超长 | ❌ | ❌ | ❌ | 原始错误摘要 |
| 已开始吐 token 后断流 | ✅（记一次） | ❌ | ❌ | 保留已输出部分，状态 FAILED |
| 用户停止 / 断连 | ❌ | ❌ | ❌ | 正常收尾（阶段 1 已实现） |
| 用户日额度耗尽 | ❌ | ❌ | ❌ | "今日额度已用完，明日 0 点重置" |
| 全局日成本超限 | ❌ | ❌ | ❌ | 同"服务暂时不可用，请稍后再试" |

---

## 4. 数据模型

新增表 `llm_usage_logs`（一次模型调用一行，含失败），Alembic 迁移 `down_revision = "f3a8c1e5b7d2"`：

| 字段 | 类型 | 说明 |
|---|---|---|
| id | int PK | |
| user_id | FK users | |
| session_id | FK chat_sessions, nullable | Agent/RAG 场景可空 |
| scene | String(16) | `chat` / `agent` / `title` / `memory` / `embedding` |
| provider | String(32) | 实际命中的 provider |
| model | String(64) | 实际命中的模型 |
| status | String(16) | `success` / `failed` / `fallback` |
| prompt_tokens / completion_tokens / total_tokens | int | 来自上游 usage，缺失为 0 |
| cost_micros | int | 按价格表折算；未知模型为 0 |
| latency_ms | int | 从发起到结束 |
| created_at | DateTime | |

索引：`(user_id, created_at)`、`(created_at)`。

---

## 5. 迭代拆分与验收标准

### 迭代 2A：熔断器

**改动**：新增 `app/core/circuit_breaker.py`；`retry.py` 每次尝试前检查、熔断打开即停止重试；`config.py` 加三个阈值；测试 `tests/test_circuit_breaker.py`。

**验收**：
- [x] 连续 5 次可重试错误 → `OPEN`，下一次调用不再发起请求（立即返回明确错误，不再等超时）
- [x] 401 / 400 连续出现不打开熔断
- [x] 冷却后只放 1 个探测请求；探测成功回 `CLOSED`，失败重新 `OPEN`
- [x] Redis 不可用时退化为进程内状态，行为不变
- [x] `pytest tests/test_retry_policy.py tests/test_circuit_breaker.py` 全绿

### 迭代 2B：备用模型降级

**改动**：`ai_client.py` 增加目标列表解析；`llm.py` 流式/非流式入口按目标顺序尝试（仅首 token 前）；`config.py` 加 `llm_fallback_provider` / `llm_fallback_model`；测试 `tests/test_model_fallback.py`。

**验收**：
- [x] 主目标连续失败且重试耗尽 → 自动切备用目标并成功出字，日志含 `fallback` 与原因
- [x] 用户自带 Key 时不跨供应商（断言备用目标与主目标同 provider）
- [x] 首个 token 已产出后失败 → 不切换，落 FAILED（与阶段 1 语义一致）
- [x] 401 不触发切换
- [x] `pytest tests/test_model_fallback.py` 全绿

### 迭代 2C：Token 预算与成本账本

**改动**：新增 `app/core/model_pricing.py`、`llm_usage_logs` 模型 + Alembic 迁移、记账与预算检查（`chat/services/messages.py`、`agent/service.py`）、`config.py` 加额度/单价相关项；测试 `tests/test_cost_budget.py`。

**验收**：
- [x] 每次生成结束写一条账本，金额与价格表一致
- [x] 未知模型不崩溃，`cost_micros=0` 且标记可查
- [x] 用户日额度设为很小值 → 超限请求被拒并给出重置时间；额度为 0 时不限制
- [x] 全局日成本超限 → 拒绝新请求
- [x] Redis 不可用时预算计数降级为进程内计数（重启清零，明确接受）
- [x] `pytest tests/` 全量绿（310 passed）

---

## 6. 测试计划

| 文件 | 覆盖 |
|---|---|
| `tests/test_circuit_breaker.py` | 状态机全流转（CLOSED→OPEN→HALF_OPEN→CLOSED/OPEN）、只统计可重试错误、Redis 降级、并发探测只放一个 |
| `tests/test_model_fallback.py` | 主目标失败切备用、用户 Key 不跨供应商、首 token 后不切换、401 不切换 |
| `tests/test_cost_budget.py` | 价格表前缀匹配、未知模型、账本落库字段、用户/全局额度拒绝、Redis 降级 |

沿用 `conftest.py` 现有模式（独立测试库、模块级 mock Redis、`asyncio.run()` 包裹），不引入 pytest-asyncio。

**故障演练（手动）**：把 `DEEPSEEK_BASE_URL` 指向一个返回 500 的本地桩服务，连打 6 次，观察第 6 次是否在 1 秒内返回且日志出现 `circuit open`；冷却后观察探测放行。

---

## 7. 变更文件清单

| 动作 | 文件 | 迭代 |
|---|---|---|
| 新增 | `backend/app/core/circuit_breaker.py` | 2A |
| 修改 | `backend/app/core/retry.py` | 2A |
| 修改 | `backend/app/core/config.py` | 2A/2B/2C |
| 修改 | `backend/app/core/ai_client.py` | 2B |
| 修改 | `backend/app/chat/services/llm.py` | 2B/2C |
| 新增 | `backend/app/core/model_pricing.py` | 2C |
| 新增 | `backend/app/usage/models/__init__.py`（`LlmUsageLog`） | 2C |
| 新增 | `backend/app/usage/service.py`（记账 + 预算闸门） | 2C |
| 新增 | `backend/alembic/versions/c8f1a3d5e7b9_add_llm_usage_logs.py` | 2C |
| 修改 | `backend/app/chat/services/messages.py`、`backend/app/agent/service.py` | 2C |
| 新增 | `backend/tests/test_circuit_breaker.py`、`test_model_fallback.py`、`test_cost_budget.py` | 各迭代配套 |

**明确不做**（本阶段）：动态路由（按任务难度选模型）、供应商价格自动同步、按用户自定义价格、多副本熔断的强一致性（近似即可）、中途掐断已开始的流。

---

## 8. 风险与注意事项

1. **熔断阈值过低会把偶发抖动放大成人为故障**：默认 5 次、只统计可重试类错误，且 HALF_OPEN 必须放探测而不是永久拒绝。
2. **降级会改变回答风格与能力**：响应/日志必须记录实际命中的 provider+model，前端可用 `usage.model` 展示，避免"同一条提问前后表现不一致却无从解释"。
3. **记账顺序**：先落 PG 账本、再累加 Redis 计数；权威账目是 PG，Redis 只是闸门。两者不一致时以 PG 为准，不做自动补偿。
4. **用户 Key 与系统 Key 不能串账**：任何降级目标都必须继承"Key 归属"，不能出现拿用户 Key 拼系统 provider 或反之。
5. **Celery 侧（标题/记忆提取）暂不接入降级链**：后台任务失败可重跑，先只记账不改路由，避免一次性铺得太开。

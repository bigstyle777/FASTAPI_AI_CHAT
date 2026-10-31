# 阶段 3 · Agent 工程化实施方案

> 版本：v1
> 日期：2026-10-31
> 前置：阶段 1（可靠聊天）、阶段 2（模型调用治理）已验收
> 原则：模块化改造、保留单体、最小改动、每步配套测试

---

## 0. 一句话目标

让 Agent 跑得像聊天一样可靠：**同一请求只跑一次、失败有边界、超时有上限、断连能收尾、每次模型调用都受治理并记账。**

---

## 1. 现状盘点（差距）

| # | 现状 | 位置 | 风险 |
|---|---|---|---|
| 1 | planner / executor / finalizer 全部裸调 `client.chat.completions.create` | [planner.py](../../backend/app/agent/planner.py#L68-L73)、[executor.py](../../backend/app/agent/executor.py#L193-L196)、[finalizer.py](../../backend/app/agent/finalizer.py#L28-L33) | 上游抖动时 Agent 整体失败，绕过阶段 2 的重试、熔断、降级 |
| 2 | 只把最终回答的 usage 落账 | [agent.py](../../backend/app/agent/agent.py#L150-L162) | 规划 + 每步执行 + 工具轮次的 token 全部漏记，成本严重低估 |
| 3 | 无整轮超时 | [agent.py](../../backend/app/agent/agent.py#L38-L49) 只有 `max_steps=6` | 单个工具/上游卡住时长时间挂起 |
| 4 | 无幂等、无每用户单并发 | [router.py](../../backend/app/agent/router.py#L21-L32)、[service.py](../../backend/app/agent/service.py#L93-L121) | 双击/重试会重复执行工具与重复扣费 |
| 5 | 无客户端断连检测 | [service.py](../../backend/app/agent/service.py#L166-L178) | 用户关页面后仍跑完整计划 |
| 6 | 步骤失败后继续跑剩余步骤 | [agent.py](../../backend/app/agent/agent.py#L94-L145) | 在错误结果上继续烧 token，还可能执行危险工具 |
| 7 | 工具调用无超时、无输出上限 | [tool_calling.py](../../backend/app/agent/tool_calling.py#L110-L192) | 一个挂死的 MCP / 超大返回值拖垮整轮 |
| 8 | planner 无格式失败重试、不校验工具名 | [planner.py](../../backend/app/agent/planner.py#L44-L94) | 模型编造工具名 / 坏 JSON 直接让整轮失败 |
| 9 | 运行状态读改写、无启动清扫 | [repo.py](../../backend/app/agent/repo.py#L28-L62)、[main.py](../../backend/app/main.py#L37-L45) | 进程崩溃后遗留永久 `running` 记录 |
| 10 | trace 缺 token / 错误分类 | [trace.py](../../backend/app/agent/trace.py#L43-L120) | 排障只能看耗时，看不出成本与失败类型 |

---

## 2. 设计决策（已确认）

1. **步骤失败即停并总结**：失败后不再执行剩余步骤，带已完成结果进入总结阶段；用户仍拿到当前结论。
2. **整轮超时 120 秒**（可配置）：在步骤边界与流式 token 边界生效，超时落 `timed_out` 并保留部分结果。
3. **Agent 全链路接入阶段 2 治理**：规划、执行、总结都走 `ModelTarget` 链（重试 + 熔断 + 按 Key 归属降级）。
4. **工具循环不跨目标重放**：工具调用可能已有副作用，失败即停后由总结阶段在备用目标收尾，不重新执行工具。
5. **运行状态机**：`running → completed / failed / stopped / timed_out`；只允许从 `running` 流转，条件 UPDATE。
6. **幂等**：`Idempotency-Key` + `agent_runs.idempotency_key` 唯一约束；重复请求直接返回明确提示，不重复执行。
7. **工具超时 15 秒、输出上限 4000 字符**（均可配置）；超时/截断都作为工具失败交给模型修正。
8. **Token 记账覆盖全链路**：planner / 每步 LLM / finalizer 的 usage 累加后，一次性写入 `agent_runs` 与 `llm_usage_logs`。

---

## 3. 迭代拆分

### 3A · 运行生命周期（幂等 / 超时 / 断连 / 收尾）

- `AgentRun` 增加 `idempotency_key`（唯一、可空）与迁移。
- `create_agent_run` 重复键返回 `None`；service 返回 SSE 错误“请求已提交，请勿重复”。
- 入口加**每用户单运行**（DB 查 `status='running'`）与**全局并发闸门**（Semaphore，满了直接拒绝）。
- 整轮 deadline：步骤间检查，超时停止后续步骤、仍执行总结；`AgentState.status = "timed_out"`。
- 客户端断连：router 传 `request.is_disconnected`，service 降频检查后标记停止，收尾照常落库。
- 启动清扫：`running` 超过 N 分钟 → `failed`（“服务重启，运行中断”）。

**验收**
- [x] 同一 `Idempotency-Key` 第二次请求返回错误，`agent_runs` 只有一条。
- [x] 运行超过 deadline → 不再执行后续步骤，状态 `timed_out`，已回答内容保留。
- [x] 客户端断连 → 状态 `stopped`，不会遗留 `running`。
- [x] 进程重启后遗留 `running` 被清扫为 `failed`。

### 3B · 模型调用治理与全链路记账

- 新增 `core/llm_gateway.py`：`complete()`（非流式）与 `stream_completion()`（流式，仅首 chunk 前可换目标）。
- planner / executor / finalizer 接受 `targets`；未传 targets 时保持裸 client 路径（兼容现有测试）。
- executor 的工具循环只带主目标熔断标识，不跨目标重放。
- usage 累加：planner、每轮 tool loop、finalizer 的 usage 汇总后随最终 `StreamUsageEvent` 下发；`TokenUsage` 带 `provider`。
- `agent_runs` 与 `llm_usage_logs` 均写全量 token。

**验收**
- [x] 主目标连续失败 → planner/finalizer 在备用目标成功，运行不失败。
- [x] 一次 agent 运行的账本 `total_tokens` ≥ 规划 + 执行 + 总结三段之和（不为 0）。
- [x] 401 不触发切换。

### 3C · 规划与执行健壮性

- planner：JSON 解析失败自动重试 1 次；过滤不存在的工具名；步骤数截断到 `max_steps`。
- 步骤失败即停，进入总结；`AgentState.error` 记录失败原因。
- 工具执行 `asyncio.wait_for` 超时；结果超出上限截断并标注。
- trace：LLM span 记录 token、provider、错误类型。

**验收**
- [x] 规划器首次返回坏 JSON → 第二次成功，整轮不失败。
- [x] 工具超时 → 步骤失败、不再执行后续步骤，总结仍产出。
- [x] 超大工具结果被截断到上限，模型能继续处理。

---

## 4. 新增配置

| 配置 | 默认 | 说明 |
|---|---|---|
| `agent_run_timeout_seconds` | 120 | 整轮软超时 |
| `agent_max_concurrent_runs` | 4 | 全局并发上限 |
| `agent_tool_timeout_seconds` | 15 | 单工具执行超时 |
| `agent_tool_output_max_chars` | 4000 | 单工具结果上限 |
| `agent_planner_max_attempts` | 2 | 规划最多尝试次数（含首次） |

`max_steps=6` / `max_tool_turns=5` 维持现有入口默认值。

---

## 5. 变更文件清单

| 类型 | 文件 | 迭代 |
|---|---|---|
| 新增 | `backend/app/core/llm_gateway.py` | 3B |
| 新增 | `backend/alembic/versions/d9e2b4f6a8c0_add_agent_run_idempotency.py` | 3A |
| 修改 | `backend/app/core/config.py` | 3A/3C |
| 修改 | `backend/app/core/retry.py`（切换目标判定） | 3B |
| 修改 | `backend/app/agent/models/__init__.py`（idempotency_key） | 3A |
| 修改 | `backend/app/agent/repo.py`（幂等创建/并发查询/清扫） | 3A |
| 修改 | `backend/app/agent/planner.py` | 3B/3C |
| 修改 | `backend/app/agent/executor.py` | 3B/3C |
| 修改 | `backend/app/agent/finalizer.py` | 3B |
| 修改 | `backend/app/agent/agent.py`（deadline/失败即停/usage 汇总） | 3A/3C |
| 修改 | `backend/app/agent/service.py`（幂等/闸门/断连/状态映射） | 3A |
| 修改 | `backend/app/agent/router.py`（Header + disconnect） | 3A |
| 修改 | `backend/app/main.py`（启动清扫） | 3A |
| 修改 | `backend/app/agent/tool_calling.py`（超时/截断/usage） | 3C |
| 新增 | `backend/tests/test_agent_governance.py` | 3A/3B/3C |

---

## 6. 风险与边界

- 软超时不打断执行中的单次工具/上游调用；这段时间由工具超时（15s）与客户端 60s 超时兜底。
- 工具循环不跨目标重放，牺牲“步骤内降级”换取“不重复执行副作用”；总结阶段仍可降级。
- 幂等重复请求返回错误而非重放流：Agent 事件流没有可重放的持久化形态，明确拒绝比假重放更安全。
- 旧数据没有 `idempotency_key`，迁移保持可空，不影响历史运行记录。

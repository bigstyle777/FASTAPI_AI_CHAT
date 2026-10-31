# 阶段 4 · 生产运维实施方案

> 版本：v1
> 日期：2026-10-31
> 前置：阶段 1（可靠聊天）、阶段 2（模型治理）、阶段 3（Agent 工程化）已验收
> 原则：模块化改造、保留单体、最小改动、每步配套测试

---

## 0. 一句话目标

让服务**上线后可判断、可观测、可恢复**：进程活没活、依赖好不好、慢在哪、失败多少，出问题时能定位、能备份、能回滚。

---

## 1. 现状盘点（差距）

Docker/Compose、CI、密钥挂载、日志轮转已经是可用状态，缺口集中在应用内部。

| # | 现状 | 位置 | 风险 |
|---|---|---|---|
| 1 | 无健康检查路由，Compose 只能打 `/` | [main.py](../../backend/app/main.py#L63-L66)、[docker-compose.yml](../../docker-compose.yml#L104-L109) | 依赖已挂但容器仍显示 healthy，负载均衡不会摘流 |
| 2 | 无任何运行指标 | 全局 | 排障只能翻日志，无法判断 LLM 失败率、SSE 并发、队列积压趋势 |
| 3 | 日志是纯文本，无 JSON 开关 | [logging.py](../../backend/app/core/logging.py#L20-L64) | 日志采集系统难以结构化解析（虽然有 request_id） |
| 4 | DB 引擎未配置连接池 | [database.py](../../backend/app/core/database.py#L9) | 高并发下连接数不可控，网络断连后无 pre-ping 自愈 |
| 5 | Celery 仅 `task_track_started` | [celery_worker.py](../../backend/app/core/celery_worker.py#L21) | 任务无超时上限、无迟到确认，worker 被 SIGTERM 时可能丢任务 |
| 6 | RAG 文档任务无异常处理、无重试 | [rag/tasks.py](../../backend/app/rag/tasks.py#L6-L13) | 解析/嵌入失败一次即终止，用户文档永远停在“处理中” |
| 7 | 记忆任务吞异常返回成功结果 | [memory_tasks.py](../../backend/app/task/memory_tasks.py#L42-L46) | Celery 侧看不到失败，只能靠日志 |
| 8 | 停机只关 MCP 连接，SSE 无收尾信号 | [main.py](../../backend/app/main.py#L62-L63) | 滚动发布时长连接被硬断，前端看到错误而非正常结束 |
| 9 | 限流无条件信任 `X-Forwarded-For` | [user/router.py](../../backend/app/user/router.py#L40-L45) | 伪造请求头可绕过注册/验证码限流 |
| 10 | 无备份/恢复脚本与运行手册 | 仓库根目录 | 数据损坏时只能手工操作，恢复流程不可演练 |
| 11 | Compose 未设置停机宽限期 | [docker-compose.yml](../../docker-compose.yml#L69-L74) | 默认 10s 后 SIGKILL，生成任务可能被砍断 |

---

## 2. 设计决策（已确认）

1. **指标自建轻量 `/metrics`**：用标准库输出 Prometheus 文本格式，不新增依赖；只暴露 HTTP、LLM、SSE、Agent、Celery 队列五类指标。
2. **健康检查分级**：`/health/live` 只看进程；`/health/ready` 中 DB/Redis 关键失败返回 503，Celery 积压、MCP 异常只标记 `degraded` 并返回 200。
3. **Celery 分任务治理**：worker 级开启 `acks_late` 与预取 1；RAG 文档任务有限重试 + 软超时；记忆任务保持“吞异常返回结果”（不打扰用户），保留 error 日志。
4. **包含备份/恢复脚本与运行手册**：`pg_dump` / `pg_restore` 脚本带保留策略，手册覆盖发布、回滚、排障、备份恢复。
5. **可信代理白名单**：默认忽略 `X-Forwarded-For`；仅当直连方 IP 在 `TRUSTED_PROXY_IPS` 中（或配置为 `*`）时才采信。
6. **结构化日志默认关闭**：`LOG_JSON=false` 保持本地可读；容器/生产通过环境变量开启。
7. **DB 连接池**：`pool_pre_ping` 常开，池大小/溢出/回收时间配置化。
8. **优雅停机**：应用内维护 `shutting_down` 标志；lifespan 退出时置位，SSE 主循环检测后正常收尾并落库，而不是被客户端断连兜底。

---

## 3. 迭代拆分

### 4A · 健康检查、连接池与优雅停机

- 新增 `core/health.py`：`GET /health/live`、`GET /health/ready`（DB `SELECT 1`、Redis `PING`、Celery 队列长度、MCP unhealthy 列表）。
- 新增 `core/lifecycle.py`：`mark_shutting_down()` / `is_shutting_down()`。
- lifespan 退出时先置停机标志，再关 MCP；compose 增加 `--timeout-graceful-shutdown`。
- 聊天与 Agent 的 `should_stop` 合并停机标志。
- `database.py` 接入连接池配置；Compose healthcheck 改打 `/health/ready`。

**验收**
- [x] `/health/live` 恒 200；DB 或 Redis 不可用 → `/health/ready` 503。
- [x] Celery 队列积压或 MCP 异常 → 200 且 `status=degraded`。
- [x] 停机标志置位后，进行中的 SSE 生成在下一个检查点正常结束。
- [x] 测试库连接池参数生效（可断言 engine 配置）。

### 4B · Celery 可运维 + 限流入口安全

- Celery conf：`task_acks_late=True`、`worker_prefetch_multiplier=1`、`task_time_limit`、`task_soft_time_limit`、`broker_transport_options.visibility_timeout`。
- RAG `process_document_task`：`autoretry_for=(Exception,)`、`max_retries`、指数退避 + 抖动；记录最终失败日志。
- 记忆任务：保留返回结构化结果，补充失败 error 日志（含 user_id / task 名）。
- `_get_client_ip` 改为可信代理判定，默认忽略伪造头。
- Compose worker 增加 `stop_grace_period`。

**验收**
- [x] RAG 任务具备重试元数据与软超时；重试耗尽后 Celery 标记失败且日志可查。
- [x] Celery conf 关键项可在测试中断言。
- [x] 默认配置下伪造 `X-Forwarded-For` 不影响限流键；白名单内的代理才被采信。

### 4C · 指标、结构化日志与运维交付

- 新增 `core/metrics.py`（Counter/Gauge/Histogram + Prometheus 文本渲染，线程安全）。
- `GET /metrics`：HTTP 请求计数/耗时、LLM 调用结果、SSE 活跃连接、Agent 运行状态、Celery 队列长度。
- HTTP 中间件与 LLM 重试入口埋点；聊天/Agent 服务维护 SSE 活跃数与运行结果计数。
- `logging.py` 增加 `LOG_JSON` 开关，JSON 行包含 timestamp/level/logger/request_id/message。
- 新增 `scripts/backup_db.ps1`、`scripts/restore_db.ps1`（保留 N 天、恢复前确认）。
- 新增 `docs/operations-runbook.md`：发布/回滚、健康与指标含义、常见故障处置、备份恢复、日志与 Celery 排障。
- 更新 `.env.example` 与 `docker-compose.yml`。

**验收**
- [x] `/metrics` 输出合法 Prometheus 文本，包含 HTTP/LLM/SSE/Agent/队列五类指标。
- [x] 一次请求后 HTTP 计数与耗时分桶增加。
- [x] `LOG_JSON=true` 时每行是合法 JSON 且含 request_id；默认仍为文本。
- [x] 备份脚本生成带时间戳的 `.sql.gz`，按保留天数清理；恢复脚本有破坏性操作确认。
- [x] 运行手册覆盖至少 5 类故障场景的处理步骤。

---

## 4. 新增配置

| 配置 | 默认 | 说明 |
|---|---|---|
| `database_pool_size` | 10 | SQLAlchemy 连接池常驻连接数 |
| `database_max_overflow` | 20 | 峰值额外连接数 |
| `database_pool_recycle_seconds` | 1800 | 连接回收，规避服务端空闲断连 |
| `database_pool_pre_ping` | true | 取连接前探活 |
| `celery_task_soft_time_limit_seconds` | 120 | 任务软超时（可捕获收尾） |
| `celery_task_time_limit_seconds` | 180 | 任务硬超时 |
| `celery_task_max_retries` | 3 | RAG 任务最大重试次数 |
| `celery_retry_backoff_seconds` | 10 | 重试指数退避基数 |
| `celery_queue_backlog_threshold` | 100 | 队列积压告警阈值（degraded） |
| `metrics_enabled` | true | `/metrics` 开关 |
| `log_json` | false | 结构化 JSON 日志开关 |
| `trusted_proxy_ips` | 空 | 可信代理 IP 列表，逗号分隔，`*` 表示全信任 |
| `shutdown_grace_seconds` | 30 | 停机宽限期（Compose 与 uvicorn 对齐） |

---

## 5. 变更文件清单

| 类型 | 文件 | 迭代 |
|---|---|---|
| 新增 | `backend/app/core/health.py` | 4A |
| 新增 | `backend/app/core/lifecycle.py` | 4A |
| 新增 | `backend/app/core/metrics.py` | 4C |
| 新增 | `backend/scripts/backup_db.ps1` | 4C |
| 新增 | `backend/scripts/restore_db.ps1` | 4C |
| 新增 | `docs/operations-runbook.md` | 4C |
| 修改 | `backend/app/core/config.py` | 4A/4B/4C |
| 修改 | `backend/app/core/database.py` | 4A |
| 修改 | `backend/app/main.py`（健康路由/停机标志/中间件指标/JSON 日志） | 4A/4C |
| 修改 | `backend/app/core/logging.py` | 4C |
| 修改 | `backend/app/core/celery_worker.py` | 4B |
| 修改 | `backend/app/rag/tasks.py` | 4B |
| 修改 | `backend/app/task/memory_tasks.py` | 4B |
| 修改 | `backend/app/user/router.py`（可信代理） | 4B |
| 修改 | `backend/app/chat/services/messages.py`（停机/SSE 指标） | 4A/4C |
| 修改 | `backend/app/agent/service.py`（停机/SSE/运行指标） | 4A/4C |
| 修改 | `backend/app/core/retry.py`（LLM 指标） | 4C |
| 修改 | `docker-compose.yml` | 4A/4B/4C |
| 修改 | `.env.example` | 4C |
| 新增 | `backend/tests/test_health_and_metrics.py` | 4A/4C |
| 新增 | `backend/tests/test_ops_config.py` | 4B/4C |

---

## 6. 风险与边界

- `/metrics` 无鉴权，默认只部署在内网/本机回环；如需公网暴露，应由反向代理加访问控制。
- 连接池参数只对 PostgreSQL 生效；测试库仍使用 conftest 自建引擎，不受影响。
- 指标为进程内聚合，多副本部署时需在采集侧按实例聚合；Celery worker 的进程内指标不会被 backend 抓到，worker 侧只记录日志。
- 停机标志只覆盖 SSE 主循环的检查点，不强制中断正在等待的上游调用；硬超时由 uvicorn 宽限期兜底。
- 备份脚本依赖运行中的 `aichat-postgres` 容器名，改名部署需同步调整参数。

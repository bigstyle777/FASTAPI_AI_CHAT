# 生产运行手册

> 适用版本：阶段 4（生产运维）之后
> 服务组成：`backend`（FastAPI + SSE）、`worker`（Celery）、`db`（PostgreSQL+pgvector）、`redis`、`frontend`（Nginx）

---

## 1. 发布与回滚

### 发布

```powershell
# 1) 构建并启动（alembic 迁移在 backend 容器启动命令里自动执行）
docker compose build backend worker frontend
docker compose up -d

# 2) 确认依赖与业务都就绪
curl http://localhost:8001/health/ready
curl http://localhost:8001/metrics
```

- 迁移失败时 backend 容器起不来，`docker compose logs backend` 可看到 alembic 报错。
- `worker` 与 `backend` 共用镜像，发布时一起重建，避免任务代码与 API 版本错位。

### 回滚

```powershell
# 1) 回退镜像标签（前提：发布时用不可变 tag，而不是 latest）
docker compose down
# 修改 docker-compose.yml 中 image 指向旧版本，再启动
docker compose up -d
```

- 数据库迁移默认只做向前兼容的加列/加表；如必须回退 schema：
  `docker exec aichat-backend alembic -c backend/alembic.ini downgrade -1`
  **先确认没有新版本写入的数据依赖新列，再执行。**
- 回滚后重新检查 `/health/ready` 与最近一次 `agent_runs` / `chat_generations` 状态。

---

## 2. 健康检查

| 端点 | 语义 | 失败表现 |
|---|---|---|
| `GET /health/live` | 进程存活，不探测依赖 | 进程死掉 → 连接被拒 |
| `GET /health/ready` | 就绪判定，分级 | DB/Redis 不可用 → 503；Celery 积压/MCP 异常 → 200 + `degraded` |

`/health/ready` 返回结构：

```json
{
  "status": "ok | degraded | unhealthy",
  "checks": {
    "database": {"ok": true, "detail": "ok"},
    "redis": {"ok": true, "detail": "ok"},
    "celery": {"status": "ok", "queue_length": 3},
    "mcp": {"unhealthy_servers": {}}
  }
}
```

- `unhealthy`：编排层应摘流/重启；Compose 的 backend healthcheck 打的就是这个端点。
- `degraded`：边缘组件异常，核心链路仍可用，先排查不要急着重启。

---

## 3. 指标（`GET /metrics`）

Prometheus 文本格式，进程内聚合。多副本按实例分别抓取。

| 指标 | 含义 | 建议告警 |
|---|---|---|
| `aichat_http_requests_total{method,path,status}` | HTTP 请求量 | 5xx 比例 > 1% |
| `aichat_http_request_duration_seconds` | 请求耗时直方图 | P95 > 2s（SSE 路径除外） |
| `aichat_llm_calls_total{provider,model,outcome}` | 模型调用结果 | 非 success 比例 > 5%（5 分钟） |
| `aichat_llm_call_duration_seconds` | 模型调用耗时 | P95 接近 60s 上游超时 |
| `aichat_sse_active_connections` | 当前 SSE 连接数 | 持续接近并发闸门（chat 20 / agent 4） |
| `aichat_agent_runs_total{status}` | Agent 运行终态 | `failed`/`timed_out` 持续增长 |
| `aichat_celery_queue_length{queue}` | 默认队列积压 | > 100（同时 `/health/ready` 变 degraded） |

`/metrics` 无鉴权，只部署在内网或由反向代理加访问控制。

---

## 4. 常见故障处置

### 4.1 Redis 不可用

- 表现：`/health/ready` 503；登录态、验证码、限流、停止信号走内存降级（单进程有效）。
- 处置：`docker compose ps redis` → 查看 `docker compose logs redis` → 恢复后等待 30s（客户端每 30s 重试一次）。
- 注意：降级期间多副本之间的状态不共享，不要在此期间扩容副本。

### 4.2 PostgreSQL 不可用

- 表现：`/health/ready` 503、接口 500、连接池等待。
- 处置：`pg_isready -U postgres -d aichat`；空间满时清理 `docker system df` 与旧备份；恢复后连接池 `pre_ping` 会自动重建连接。
- 恢复后检查：`chat_generations` 与 `agent_runs` 是否有遗留 `running`（启动清扫只在新进程启动时执行）。

### 4.3 Celery 积压或任务失败

- 查看队列：`docker exec aichat-redis redis-cli -a "$REDIS_PASSWORD" --no-auth-warning llen celery`
- 查看 worker：`docker compose logs worker --tail 200`，关注 `document_id` / `user_id`
- RAG 任务失败会自动重试（默认 3 次，指数退避）；重试耗尽后文档状态为 `failed`，用户可重新上传。
- 记忆任务失败不重试，只记 error 日志；必要时重新触发提取。
- 扩容：`docker compose up -d --scale worker=2`（共享上传卷，注意磁盘）。

### 4.4 LLM 熔断 / 降级

- 表现：日志出现 `AI 服务暂时不可用（provider 上游熔断中）`；`aichat_llm_calls_total` 失败升高。
- 处置：确认上游 Key 额度与状态；熔断冷却 60s 后自动 HALF_OPEN 探测，无需重启。
- 需要临时关闭降级：设置 `LLM_FALLBACK_ENABLED=false` 后重启 backend。

### 4.5 SSE 卡住或长时间不结束

- 表现：`aichat_sse_active_connections` 不下降；前端无新 token。
- 处置顺序：
  1. 看 backend 日志的 `request_id`，确认卡在模型调用还是工具调用；
  2. 模型调用有 60s 请求超时、流式总预算 300s，Agent 整轮 120s，正常情况下会自行收尾；
  3. 确认客户端是否已断连（日志 `客户端断连，停止生成`）；
  4. 以上都正常仍卡住 → 重启 backend（停机标志会让进行中的流在下一个检查点收尾）。

### 4.6 MCP 工具异常

- 表现：`/health/ready` 的 `mcp.unhealthy_servers` 非空；日志 `MCP server 连接异常`。
- 处置：检查对应 MCP Server 进程/端口；单个 server 不影响其他工具与聊天主链路。

---

## 5. 备份与恢复

### 备份（宿主机执行，依赖容器名 `aichat-postgres`）

```powershell
.\backend\scripts\backup_db.ps1                 # 默认保留 7 天
.\backend\scripts\backup_db.ps1 -KeepDays 14 -OutputDir D:\backups
```

产物：`backups/aichat_<时间戳>.sql.gz`。

### 恢复（破坏性操作，会先删对象再重建）

```powershell
.\backend\scripts\restore_db.ps1 -File .\backups\aichat_20261031_120000.sql.gz
# 演练/自动化可加 -Force 跳过确认
```

恢复后必须：

1. 重启 backend 与 worker（清掉连接池与残留内存状态）；
2. 访问 `/health/ready`；
3. 抽查登录、聊天、Agent 各一条链路。

Linux 服务器等价的直接命令：

```bash
docker exec aichat-postgres pg_dump -U postgres -d aichat --clean --if-exists | gzip > aichat_$(date +%Y%m%d_%H%M%S).sql.gz
gunzip -c aichat_xxx.sql.gz | docker exec -i aichat-postgres psql -U postgres -d aichat -v ON_ERROR_STOP=1
```

---

## 6. 日志与排障

- 每个请求有 `request_id`，文本格式：`[abc12345]`；`LOG_JSON=true` 时是 JSON 字段 `request_id`。
- 线上建议 `LOG_JSON=true`，由采集系统按 `level`、`logger`、`request_id` 建索引。
- 关键日志关键词：
  - `access`：请求方法/路径/状态/耗时/用户
  - `LLM 调用失败（第 N/M 次）`：重试
  - `上游熔断中`：熔断
  - `客户端断连` / `服务停机`：SSE 收尾原因
  - `Agent 运行失败`：带堆栈
- 容器日志：`docker compose logs backend --tail 500 -f`；日志已按 10MB × 3 轮转。

---

## 7. 停机与滚动发布

- `docker compose stop backend` 会给容器发 SIGTERM：
  1. lifespan 先置停机标志；
  2. 进行中的 SSE 在下一个检查点主动结束并落库；
  3. uvicorn 宽限期 30s，容器 `stop_grace_period` 40s 兜底。
- `worker` 收到 SIGTERM 后 warm shutdown，`stop_grace_period` 60s；任务完成才确认（`acks_late=True`），未完成任务会重新投递。
- 滚动发布时先扩新实例再停旧实例；本部署为单实例时，停机期间前端会短暂收到断流，属预期。

# AIChatPro

生产级全栈 AI 对话应用：SSE 流式聊天、RAG 文档问答、工具调用与 MCP、规划式 Agent、长期记忆、用量成本治理，一条 `docker compose up -d` 即可完整拉起。

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-ASGI-009688?logo=fastapi&logoColor=white)
![Vue](https://img.shields.io/badge/Vue-3-4FC08D?logo=vuedotjs&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-pgvector-4169E1?logo=postgresql&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-7-DC382D?logo=redis&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)
![Tests](https://img.shields.io/badge/tests-367%20passed-brightgreen)

## 特性

**聊天体验**
- SSE 流式输出，支持随时停止、断连自动收尾落库、刷新/双击幂等去重
- Markdown / KaTeX / Mermaid / 代码高亮渲染，多会话管理
- 长期记忆：异步抽取用户偏好与事实，检索后注入上下文

**模型与工具治理**
- 多模型接入（DeepSeek / OpenAI 兼容协议），用户级 API Key 配置
- 熔断 + 有限重试 + 指数退避抖动，失败可切换备用模型目标
- Token 用量与成本账本，预算超限自动拒答
- RAG：文档上传、pgvector 检索、Celery 异步索引
- MCP 工具生态：内置计算器，可配置接入任意 MCP Server

**Agent 工程化**
- 规划 → 并行执行 → 总结的 Agent 主循环，全程可观测（trace 落库）
- 全局并发闸门 + 每用户单运行 + 幂等键，拒绝重复扣费
- 运行超时 deadline、用户停止、服务停机三种收尾路径统一处理

**生产运维**
- 分级健康检查：`/health/live` 恒活；`/health/ready` 区分 503（DB/Redis）与 degraded（队列积压/MCP 异常）
- 自建 `/metrics`（Prometheus 文本）：HTTP / LLM / SSE / Agent / Celery 队列
- 结构化 JSON 日志（`request_id` 全链路串联）、可信代理识别防限流绕过
- 数据库备份 / 恢复脚本 + 运行手册（见 `docs/`）

## 技术栈

| 层 | 技术 |
| --- | --- |
| 后端 | FastAPI · SQLAlchemy · Alembic · Celery · SSE |
| 数据 | PostgreSQL + pgvector · Redis（缓存 / 限流 / 队列） |
| 前端 | Vue 3 · Vite · TypeScript · Pinia · Tailwind CSS 4 |
| 部署 | Docker Compose（backend / worker / db / redis / frontend-nginx） |

## 快速开始

```bash
cp .env.example .env   # 填入 POSTGRES_PASSWORD / REDIS_PASSWORD / LLM Key 等
docker compose up -d
```

- 前端入口：http://localhost:8080
- API 文档：http://localhost:8001/docs
- 健康检查：`curl http://localhost:8001/health/ready`
- 指标：`curl http://localhost:8001/metrics`

## 项目结构

```
backend/            FastAPI 应用
  app/chat/         会话与流式生成（状态机 / 幂等 / 断连检测）
  app/agent/        Agent 主循环、规划器、trace
  app/rag/          文档加载、切分、向量检索、异步索引任务
  app/mcp/          MCP Server 接入与健康状态
  app/core/         配置 / 重试 / 熔断 / 指标 / 健康检查 / 生命周期
  scripts/          数据库备份与恢复（PowerShell）
frontend/           Vue 3 SPA
docker/             postgres 镜像、nginx 配置、Linux 备份脚本
docs/               阶段方案与运行手册
```

## 测试

```bash
cd backend
python -m pytest -q   # 367 passed
```

## 文档

- [运行手册](docs/operations-runbook.md)：发布回滚、告警建议、六类故障处置、备份恢复
- [阶段 4 · 生产运维方案](docs/phase4-production-ops-plan.md)
- [代码百科](wiki/)：项目导览与 Agent / RAG 模块详解

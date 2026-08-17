# Agent 模块 Code Wiki

> 本文档对 AIChatPro 项目中新增的 **Agent 模块**(`backend/app/agent/`)进行结构化梳理,覆盖整体架构、模块职责、关键类与函数、数据模型、依赖关系、SSE 事件协议以及项目运行方式。
>
> Agent 模块实现了一套 **规划 → 执行 → 总结**(Plan → Execute → Finalize)三阶段任务型智能体框架,自带 trace 落库与 SSE 实时推送,与普通聊天共用会话、消息、工具与 LLM 基础设施。

---

## 目录

- [1. 项目整体架构](#1-项目整体架构)
  - [1.1 Agent 模块在系统中的位置](#11-agent-模块在系统中的位置)
  - [1.2 核心数据流](#12-核心数据流)
  - [1.3 目录结构](#13-目录结构)
- [2. 主要模块职责](#2-主要模块职责)
- [3. 数据模型](#3-数据模型)
- [4. 关键类与函数说明](#4-关键类与函数说明)
  - [4.1 agent.py — 主循环](#41-agentpy--主循环)
  - [4.2 planner.py — 规划器](#42-plannerpy--规划器)
  - [4.3 executor.py — 执行器](#43-executorpy--执行器)
  - [4.4 finalizer.py — 总结器](#44-finalizerpy--总结器)
  - [4.5 trace.py — 追踪器](#45-tracepy--追踪器)
  - [4.6 state.py — 状态模型](#46-statepy--状态模型)
  - [4.7 events.py — SSE 事件](#47-eventspy--sse-事件)
  - [4.8 service.py — HTTP 服务层](#48-servicepy--http-服务层)
  - [4.9 router.py — HTTP 接口](#49-routerpy--http-接口)
  - [4.10 repo.py — 数据访问](#410-repopy--数据访问)
  - [4.11 prompts.py — 提示词](#411-promptspy--提示词)
- [5. 依赖关系](#5-依赖关系)
  - [5.1 模块内部依赖](#51-模块内部依赖)
  - [5.2 对项目其他模块的依赖](#52-对项目其他模块的依赖)
  - [5.3 外部依赖](#53-外部依赖)
- [6. SSE 事件协议](#6-sse-事件协议)
- [7. 项目运行方式](#7-项目运行方式)
  - [7.1 环境准备](#71-环境准备)
  - [7.2 启动后端](#72-启动后端)
  - [7.3 启动前端](#73-启动前端)
  - [7.4 数据库迁移](#74-数据库迁移)
  - [7.5 运行示例与测试](#75-运行示例与测试)
- [8. 关键设计说明](#8-关键设计说明)

---

## 1. 项目整体架构

### 1.1 Agent 模块在系统中的位置

AIChatPro 是一个前后端分离的本地 AI 聊天应用(Vue 3 + FastAPI + PostgreSQL + Redis)。Agent 模块作为后端的一个独立子包挂载在 `backend/app/agent/`,在 `main.py` 中通过 `app.include_router(agent_router)` 注册,对外提供 `/agent/*` 前缀的 HTTP 接口。

它与普通聊天(`/chat/stream`)并列,但执行范式不同:

- **普通聊天**:单轮 Tool Calling 循环,模型自己决定是否调用工具并直接给出回答;
- **Agent**:先用 LLM 把任务拆解成计划,再逐步执行(声明了工具的步骤直接调工具,否则让 LLM 带着工具循环完成),最后由总结器汇总成最终答案,全程记录 trace。

```text
┌──────────────┐   HTTP/SSE   ┌─────────────────────────────────────────────┐
│  前端 Vue    │ ───────────► │  FastAPI 后端 (backend/app)                 │
│  ChatView    │              │                                             │
│  chat store  │ ◄─────────── │  agent/router.py  ──► agent/service.py     │
└──────────────┘   SSE 事件    │                            │                │
                              │                     run_agent_stream        │
                              │                     ├─ planner.py  规划      │
                              │                     ├─ executor.py 执行      │──► tools/ 工具注册表
                              │                     └─ finalizer.py 总结    │      (calculator/weather/
                              │                            │                │       web_search/rag_search/
                              │                     trace.py 追踪落库        │       memory_search)
                              │                     repo.py  运行记录        │
                              │                            │                │
                              │                  DeepSeek / OpenAI 兼容接口   │
                              └─────────────────────────────────────────────┘
                                       │                │
                              ┌────────▼───────┐  ┌────▼──────┐
                              │ PostgreSQL      │  │  Redis    │
                              │ agent_runs      │  │ 限流/停止 │
                              │ agent_trace_    │  │ 登录态    │
                              │ points          │  └───────────┘
                              └────────────────┘
```

Agent 模块对外提供两条通路:

1. **流式执行通路**(`POST /agent/stream`):接收用户任务,跑完整的三阶段循环,以 SSE 推送计划、步骤、工具调用、最终答案等事件;
2. **运行记录查询通路**(`GET /agent/runs`、`GET /agent/runs/{run_id}`):查看历史运行及其完整 trace 明细,用于排错与审计。

### 1.2 核心数据流

**一次 Agent 请求的完整生命周期:**

```text
POST /agent/stream {session_id, message}
  │
  ▼
agent/service.agent_stream_service()
  ├─ check_rate_limit()           Redis 限流(200 次/小时/用户)
  ├─ 校验会话归属、写入用户消息、更新会话
  ├─ enqueue_session_title_generation()   异步生成会话标题(Celery)
  ├─ load_chat_context()          加载分支上下文历史
  ├─ create_agent_run()           写入 agent_runs,status="running"
  ├─ AgentTracer(db, run.id)      创建 trace 追踪器
  ├─ _get_user_ai_settings() + _get_client()   取用户 API Key(个人中心优先,.env 兜底)
  │
  ▼
run_agent_stream()  ────────────────────────── Agent 主循环
  │
  ├─ ① 规划 create_plan()
  │     LLM(temperature=0.2, JSON mode) → PlanStep[]
  │     yield agent_plan 事件
  │
  ├─ ② 逐步执行 execute_step()  (最多 max_steps 步)
  │     ├─ 步骤声明了工具 → 快路径:直接调 execute_tool_call,不经 LLM
  │     └─ 步骤无工具     → 慢路径:run_tool_loop 让 LLM 带工具循环完成
  │                        (工具轮数耗尽 → 回退一次无工具请求)
  │     yield agent_step(started/completed/failed) + agent_tool 事件
  │
  ├─ ③ 总结 stream_final_answer()
  │     把计划+各步骤结果拼成 summary 消息 → LLM 流式生成最终答案
  │     yield delta / usage 事件
  │
  ├─ yield AgentState(整轮快照,service 层拦截不外发)
  ▼
service 层收尾
  ├─ update_agent_run()           落库 plan/answer/tokens/status
  ├─ create_message(role=assistant)   AI 回复写入聊天消息表
  ├─ enqueue_memory_extraction()     异步提取用户记忆(Celery)
  └─ yield done 事件 {run_id, status}
```

**工具调用的双路径设计**(executor.py):

```text
PlanStep
  ├─ step.tool 在 TOOL_REGISTRY 中
  │     └─ _execute_direct_tool():构造 tool_call dict → execute_tool_call()
  │        快路径,零 LLM 调用,规划器已给出参数
  └─ step.tool 为空 / 未注册
        └─ _execute_with_llm():STEP_EXECUTOR 提示词 + 之前步骤结果
           → run_tool_loop()(最多 max_tool_turns 轮)
           → 轮数耗尽 → 无工具请求兜底
```

### 1.3 目录结构

```text
backend/app/agent/
  __init__.py     包说明:规划 -> 执行 -> 总结,自带 trace 落库与 SSE 推送
  agent.py        Agent 主循环(run_agent_stream / run_agent),唯一数据流入口
  planner.py      规划器:LLM 把任务拆成 PlanStep 列表
  executor.py     执行器:执行单步(工具快路径 / LLM 工具循环慢路径)
  finalizer.py    总结器:按计划+结果流式生成最终答案
  trace.py        AgentTracer(落库)/ NullTracer(兜底),span/point/emit 三原语
  state.py        PlanStep / StepResult / AgentState 运行时状态模型
  events.py       AgentPlanEvent / AgentStepEvent / AgentToolEvent / AgentDoneEvent
  service.py      HTTP 服务层:限流、消息落库、串联主循环、输出 SSE
  router.py       /agent/stream、/agent/runs、/agent/runs/{run_id}
  repo.py         agent_runs / agent_trace_points 的 CRUD
  schemas.py      AgentRunResponse 等查询响应模型
  prompts.py      三个阶段的系统提示词

backend/app/tools/                工具层(Agent 与普通聊天共用)
  __init__.py     ALL_TOOLS(JSON Schema) + TOOL_REGISTRY(name→函数)
  calculator.py   数学运算
  weather.py      天气查询
  web_search.py   网页搜索
  rag_search.py   知识库检索(RAG)
  memory_search.py 用户记忆检索

backend/alembic/versions/
  a3b4c5d6e7f8_add_agent_tables.py   agent_runs / agent_trace_points 建表迁移

backend/tests/
  test_agent_framework.py   纯内存单测(FakeClient + StubTracer,不依赖 LLM/DB)
  test_planner.py           规划器集成测试(连接真实 LLM)

backend/examples/
  agent_demo.py             命令行示例(PrintTracer 打印 trace,不连数据库)

frontend/src/
  api/chat.ts               sendAgentMessage / fetchAgentRun
  stores/chat.ts            sendAgentMessageAction:消费 agent SSE 流
  types/index.ts            AgentPlanStep / AgentSSEEvent / AgentRun 等类型
```

---

## 2. 主要模块职责

| 文件 | 层次 | 职责 |
|---|---|---|
| `router.py` | HTTP 层 | 定义 `/agent/*` 接口,鉴权(`get_current_user`)、依赖注入、SSE 响应头 |
| `service.py` | 服务层 | 限流、消息/会话落库、标题与记忆异步任务入队、创建 run、串联主循环、SSE 编码、收尾落库、异常兜底 |
| `agent.py` | 编排层 | Agent 主循环:规划 → 逐步执行 → 总结,统一 yield 事件,管理停止与状态快照 |
| `planner.py` | 能力层 | 调 LLM 生成 JSON 计划,容错解析(JSON 围栏/前后废话/response_format 兼容),校验为 `PlanStep[]` |
| `executor.py` | 能力层 | 执行单个步骤:工具快路径直接执行;无工具时 LLM 工具循环,耗尽后无工具兜底;回调桥接 trace |
| `finalizer.py` | 能力层 | 以总结提示词 + 步骤结果消息流式生成最终答案,输出 delta/usage |
| `trace.py` | 基础设施 | `AgentTracer`:point(单点)/ span(带起止与耗时)/ emit(待推送事件)三原语写 `agent_trace_points`;`NullTracer` 空实现兜底 |
| `state.py` | 数据模型 | `PlanStep`/`StepResult`(过程对象)、`AgentState`(整轮可序列化快照) |
| `events.py` | 协议层 | Agent 专用 SSE 事件载荷,事件名 = `data.type`,与聊天事件(`delta`/`usage`/`error`)兼容 |
| `repo.py` | 数据访问 | `agent_runs` 增改查、`agent_trace_points` 查询计数 |
| `schemas.py` | 协议层 | `AgentRunResponse` / `AgentTracePointResponse` / `AgentRunListResponse` |
| `prompts.py` | 配置层 | 规划器 / 步骤执行器 / 总结器三个系统提示词模板 |

---

## 3. 数据模型

两张表由迁移 `a3b4c5d6e7f8_add_agent_tables.py` 创建,ORM 定义位于 `backend/app/models.py`。

### 3.1 AgentRun(`agent_runs` 表)

一次 agent 执行记录,用于追溯计划、状态与最终答案。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | Integer PK | 运行 ID(SSE 事件中的 `run_id`) |
| `session_id` | FK → chat_sessions.id | 所属会话(索引) |
| `user_id` | FK → users.id | 发起用户(索引) |
| `status` | String(20) | `running` / `completed` / `stopped` / `failed` |
| `user_input` | Text | 用户原始任务 |
| `plan` | JSON | 计划步骤列表(`PlanStep.model_dump()[]`) |
| `final_answer` | Text | 最终答案 |
| `model` | String(120) | 使用的模型名 |
| `prompt_tokens` / `completion_tokens` / `total_tokens` | Integer | token 用量(默认 0) |
| `error_message` | Text | 失败原因 |
| `created_at` / `updated_at` | DateTime | 时间戳 |

关系:`traces` 一对多关联 `AgentTracePoint`,`cascade="all, delete-orphan"`,按 `sequence` 排序。

### 3.2 AgentTracePoint(`agent_trace_points` 表)

运行过程中的一个 trace 点(规划、步骤、LLM 调用、工具调用、错误等),按 `run_id` 级联删除。

| 字段 | 类型 | 说明 |
|---|---|---|
| `run_id` | FK → agent_runs.id (CASCADE) | 所属运行(索引) |
| `sequence` | Integer | 运行内自增序号(由 Tracer 分配,保证时序) |
| `stage` | String(30) | 阶段:`plan` / `step` / `llm` / `tool_call` / `finalize` / `error` |
| `name` | String(120) | 节点名,如 `planner` / `step_0` / `step_1_llm` / 工具名 |
| `status` | String(20) | `started` / `completed` / `failed` |
| `step_index` | Integer? | 归属步骤下标 |
| `tool_call_id` / `tool_name` | String(120)? | 工具调用标识与名称 |
| `input_data` / `output_data` | JSON? | 输入输出载荷 |
| `error_message` | Text? | 错误信息 |
| `duration_ms` | Integer? | 耗时(span 自动记录) |
| `created_at` | DateTime | 创建时间 |

---

## 4. 关键类与函数说明

### 4.1 agent.py — 主循环

**`run_agent_stream(client, model, user_input, *, messages, context=None, tracer=None, should_stop=None, max_steps=6, max_tool_turns=5) -> Generator`**

唯一的数据流入口。逐条 yield Pydantic 事件,由 service 层转成 SSE 字符串。执行流程:

1. **规划**:在 `tracer.span("plan", "planner")` 中调用 `create_plan()`;异常 → yield `StreamErrorEvent` 并结束;空计划 → 记 failed trace 点,yield 错误事件并结束;
2. **推送计划**:yield `AgentPlanEvent(run_id, steps)`;
3. **逐步执行**:遍历 `plan[:max_steps]`,每步先检查 `should_stop()`;yield `AgentStepEvent(status="started")` → `tracer.span("step", ...)` 内调 `execute_step()` → drain 工具事件 → yield `AgentStepEvent(终态)`;成功输出与失败原因都追加进 `execution_log` 供后续步骤参考;
4. **总结**:`_build_summary_message()` 把用户问题 + 每步状态拼成一条 user 消息,调 `stream_final_answer()` 流式生成,逐个转发 `delta` / `usage` 事件并累积答案与用量;
5. **快照**:构造 `AgentState`(status 依 `should_stop()` 取 `stopped`/`completed`)并 yield——**该事件不对外发**,由 service 层拦截用于收尾。

**`run_agent(client, model, user_input, *, messages, ...) -> AgentState`**:非流式入口,消费 `run_agent_stream` 并返回最终 `AgentState`(供测试与后续同步接口),循环未产出状态时抛 `RuntimeError`。

**`_build_summary_message(user_input, plan, results) -> dict`**:拼总结阶段的 user 消息,逐行列出每步 `描述 -> 状态: 产出/错误`。

### 4.2 planner.py — 规划器

**`create_plan(client, model, messages, *, available_tools=None, max_steps=6) -> list[PlanStep]`**

- 用 `PLANNER_SYSTEM_PROMPT`(注入工具名列表与步数上限)调 LLM,`temperature=0.2`;
- 优先带 `response_format={"type": "json_object"}`,provider 不支持时捕获异常去掉该参数重试一次;
- `_extract_json()` 解析返回文本,逐条校验:非 dict 跳过、description 为空跳过;`tool` 空转 `None`,`args` 非 dict 转 `{}`。

**`_extract_json(text) -> dict`**:容错 JSON 提取——先剥 ```` ```json ```` 围栏,再取首个 `{` 到最后一个 `}` 之间的子串解析;找不到对象时抛 `ValueError`。

### 4.3 executor.py — 执行器

**`execute_step(client, model, step, *, index, context=None, tracer=None, execution_log=None, max_tool_turns=5) -> StepResult`**:路由函数。`step.tool` 存在于 `TOOL_REGISTRY` → 快路径;否则 → LLM 慢路径。`tracer` 为空时用 `NullTracer` 兜底。

**`_execute_direct_tool(step, index, context, tracer) -> StepResult`**:把 `PlanStep` 组装成 tool_call dict(`id=f"step_{index}_tool"`),调 `services/tool_calling.execute_tool_call()` 直接执行;结果 JSON 含 `error` 键(`_is_tool_error()` 判定)或抛异常 → `status="failed"`。

**`_execute_with_llm(client, model, step, index, context, tracer, execution_log, max_tool_turns) -> StepResult`**:慢路径。`_build_step_prompt()` 拼当前步骤 + 预期产出 + 之前步骤结果;在 `tracer.span("llm", f"step_{index}_llm")` 中调 `run_tool_loop()`;返回 `content=None`(工具轮数耗尽)时发一次不带 tools 的请求兜底;异常 → failed。

**`_tool_callbacks(tracer, step_index)`**:返回 `(on_tool_call, on_tool_result)` 回调对,把工具调用过程同时写入 trace 表(`tracer.point`)并投递待推送事件(`tracer.emit(AgentToolEvent)`)。

**`_is_tool_error(content) -> bool`**:尝试把工具返回的 JSON 字符串解析为 dict,含非空 `error` 键即视为失败。

### 4.4 finalizer.py — 总结器

**`stream_final_answer(client, model, messages, *, tracer=None, should_stop=None) -> Generator[StreamDeltaEvent | StreamUsageEvent]`**

- 前置 `FINALIZER_SYSTEM_PROMPT` 系统消息,流式请求(`stream=True`, `include_usage=True`);
- 在 `tracer.span("finalize", "finalizer")` 中逐块 yield `StreamDeltaEvent(content)`;
- `should_stop()` 为真时中断循环(支持用户停止);
- 收到 usage 块时记录 trace 并 yield `StreamUsageEvent`(含完整 `TokenUsage`)。

### 4.5 trace.py — 追踪器

**`class AgentTracer`** — 向 `agent_trace_points` 表追加 trace 点。

| 方法 | 说明 |
|---|---|
| `__init__(db, run_id)` | 持有 SQLAlchemy Session 与运行 ID,内部维护自增 `sequence` 与待推送事件队列 |
| `point(stage, name, *, status, step_index, tool_call_id, tool_name, input_data, output_data, error_message, duration_ms)` | 立即写一条记录并 commit |
| `span(stage, name, *, step_index, tool_name, input_data)` | 上下文管理器:进入写 `started`,正常退出写 `completed`(附耗时),异常写 `failed` 后重新抛出;`yield` 出的 `_SpanRecord` 支持 `set(value)` 记录产出、`fail(error)` 标记失败 |
| `emit(event)` | 收集一个需要推送给前端的结构化事件(如 `AgentToolEvent`) |
| `drain_events() -> list` | 取出并清空待推送事件,由主循环在步骤结束后统一 yield |

**`class _SpanRecord`**:span 内的记录载体,`value` / `failed` / `error` 三字段。

**`class NullTracer`**:不落库的兜底实现,接口与 `AgentTracer` 对齐(`run_id=0`),executor/finalizer/demo 在无 tracer 时也能跑;`examples/agent_demo.py` 的 `PrintTracer` 继承它,把 trace 打印到终端。

### 4.6 state.py — 状态模型

| 类 | 基类 | 字段 | 说明 |
|---|---|---|---|
| `PlanStep` | BaseModel | `description`、`tool`(空表示 LLM 步)、`args: dict`、`expected_output` | 计划中的一步 |
| `StepResult` | BaseModel | `index`、`step`、`status: completed/failed/skipped`、`output`、`error` | 单步执行结果 |
| `AgentState` | dataclass | `run_id`、`user_input`、`plan`、`results`、`status`、`final_answer`、`error`、三个 token 计数 | 整轮运行的可序列化快照(非流式调用与测试用) |

### 4.7 events.py — SSE 事件

| 类 | `type` 字面量 | 关键字段 |
|---|---|---|
| `AgentPlanEvent` | `agent_plan` | `run_id`、`steps: list[dict]` |
| `AgentStepEvent` | `agent_step` | `run_id`、`index`、`step: dict`、`status: started/completed/failed/skipped`、`output`、`error` |
| `AgentToolEvent` | `agent_tool` | `run_id`、`step_index`、`tool_call_id`、`tool`、`arguments`、`status`、`result`、`error`、`duration_ms` |
| `AgentDoneEvent` | `done` | `run_id`、`status: completed/stopped/failed` |

`delta` / `usage` / `error` 直接复用 `app.schemas` 中的聊天事件类型,前端 `consumeStream` 无需改动即可拿到最终答案。

### 4.8 service.py — HTTP 服务层

**`agent_stream_service(db, user, request) -> Generator[str]`**:`POST /agent/stream` 的 SSE 生成器,完整职责链:

1. **限流**:`check_rate_limit(key=f"rate_limit:agent:{user_id}", limit=200, expire_seconds=3600)`,超限 yield error 事件;
2. **前置校验与落库**:清生成状态 → 校验消息非空、会话归属 → 取分支父消息写入用户消息 → 更新会话 → 入队标题生成(Celery)→ 加载聊天上下文;
3. **准备运行**:`create_agent_run()` 落库 → `AgentTracer(db, run.id)` → 取用户 AI 设置与 client(失败抛 `BusinessError` 提示配置 API Key);
4. **驱动主循环**:`context={"db", "user_id"}` 传给工具注入;`should_stop=lambda: is_stop_requested(session_id)` 接 Redis 停止信号;遍历事件——`AgentState` 拦截保存不外发,`StreamErrorEvent` 置失败标记,`AgentPlanEvent` 存计划,`StreamUsageEvent` 存用量,`delta` 累积回复;其余 `yield sse_event(event.type, event)`;
5. **收尾**:按失败/停止/完成定 status → `update_agent_run()` 落库 → AI 回复非空时写 assistant 消息(token 计数入库)并更新会话 → 入队记忆提取 → yield `done` 事件 → 清生成状态;
6. **异常兜底**:捕获后 `logger.exception`,补写 failed trace 点与 run 状态,yield error 事件(Starlette 不吞 SSE 生成器异常的关键防线)。

### 4.9 router.py — HTTP 接口

| 方法与路径 | 说明 |
|---|---|
| `POST /agent/stream` | 流式运行 agent,`StreamingResponse` + `text/event-stream`,响应头含 `Cache-Control: no-cache`、`Connection: keep-alive`、`X-Accel-Buffering: no`(防代理缓冲) |
| `GET /agent/runs?session_id=&limit=` | 当前用户运行记录列表(含 trace 明细,`limit` 1–100 默认 20) |
| `GET /agent/runs/{run_id}` | 单次运行完整 trace(排错用),不存在或越权返回 404 |

所有接口经 `Depends(get_current_user)` 鉴权;数据查询按 `user_id` 过滤实现租户隔离。

### 4.10 repo.py — 数据访问

| 函数 | 说明 |
|---|---|
| `create_agent_run(db, *, session_id, user_id, user_input)` | 创建运行记录,`status="running"` |
| `update_agent_run(db, run_id, *, status/plan/final_answer/model/tokens/error_message)` | 仅更新传入字段(全 Optional) |
| `get_agent_run(db, run_id, user_id=None)` | 按 ID 查运行,`user_id` 非空时追加归属过滤 |
| `list_agent_runs(db, user_id, *, session_id=None, limit=20)` | 用户运行列表,按 `id` 倒序,可按会话过滤 |
| `get_trace_points(db, run_id)` | 某 run 全部 trace 点,按 `sequence` 升序 |
| `count_trace_points(db, run_id)` | trace 点计数 |

### 4.11 prompts.py — 提示词

| 常量 | 角色 | 要点 |
|---|---|---|
| `PLANNER_SYSTEM_PROMPT` | 任务规划器 | 拆解任务为步骤;确定需要工具才填 `tool`/`args`;可用工具 `{tools}` 占位;不编造工具名;步数 ≤ `{max_steps}`,能一步不拆多步;只返回固定格式 JSON |
| `STEP_EXECUTOR_SYSTEM_PROMPT` | 任务执行器 | 只做当前步不扩大范围;需要数据/计算/检索优先调工具;完成后简短中文说明产出;工具反复失败则基于已有信息给结论 |
| `FINALIZER_SYSTEM_PROMPT` | 任务总结器 | 与用户语言一致(默认中文);步骤失败如实说明不编造;可引用工具返回的数字事实但不粘贴 JSON |

---

## 5. 依赖关系

### 5.1 模块内部依赖

```text
router.py ──► service.py ──► agent.py(主循环) ──► planner.py
    │             │                │      │
    │             │                │      ├─► executor.py ──► executor 依赖:
    │             │                │      │        services/tool_calling(execute_tool_call,
    │             │                │      │        run_tool_loop) + tools(TOOL_REGISTRY)
    │             │                │      └─► finalizer.py
    │             │                └──────► trace.py(AgentTracer,被所有阶段使用)
    │             ├─► repo.py(create/update_agent_run)
    │             └─► events.py(AgentDoneEvent 等)
    └─► repo.py(list/get_agent_run) + schemas.py(响应模型)

state.py:被 agent/executor/planner 引用(纯数据模型,无外部依赖)
events.py:被 agent/executor/service 引用;prompts.py:被 planner/executor/finalizer 引用
```

### 5.2 对项目其他模块的依赖

| 依赖 | 用途 |
|---|---|
| `app.tools`(TOOL_REGISTRY / ALL_TOOLS) | 工具名合法性校验与工具执行;新增工具只需在 `tools/` 加模块并登记,agent 自动生效 |
| `app.services.tool_calling` | `execute_tool_call`(单次工具执行 + 回调)与 `run_tool_loop`(LLM 工具循环) |
| `app.services.llm` | `_get_user_ai_settings` / `_get_client`:个人中心 API Key 优先、`.env` 兜底 |
| `app.services.cache` | `check_rate_limit`(Redis 限流)、`is_stop_requested`(停止信号)、`clear_generation_status` |
| `app.services.message_context` | `load_chat_context`(分支上下文)、`get_branch_parent_message_id` |
| `app.services.task.title_queue` / `memory_queue` | Celery 异步任务:会话标题生成、用户记忆提取 |
| `app.crud` | `create_message` / `get_session_by_user` / `update_session` |
| `app.core.sse` | `sse_event()` 统一 SSE 编码 |
| `app.core.database` | `get_db` 会话依赖 |
| `app.services.auth` | `get_current_user` 鉴权 |
| `app.models` | `AgentRun` / `AgentTracePoint` ORM |
| `app.schemas` | `ChatRequest` 请求体;`StreamDeltaEvent` / `StreamUsageEvent` / `StreamErrorEvent` / `TokenUsage`(与聊天流复用) |

### 5.3 外部依赖

- **openai SDK**(`client.chat.completions.create`):兼容 DeepSeek / OpenAI 及任意 OpenAI 兼容接口;client 由上层注入,agent 模块本身不创建连接;
- **FastAPI / Starlette**:路由、依赖注入、`StreamingResponse`;
- **SQLAlchemy**:ORM 与数据访问;
- **Pydantic**:事件、状态与响应模型;
- **Redis**(经 `services.cache`):限流与停止信号;
- **Celery**(经 title/memory 队列):异步旁路任务。

---

## 6. SSE 事件协议

事件命名约定与聊天流一致:**SSE `event` 名 = `data` 里的 `type` 字段**。一次完整 agent 流的典型事件序列:

```text
event: agent_plan    data: {"type":"agent_plan","run_id":1,"steps":[{description,tool,args,expected_output},...]}
event: agent_step    data: {"type":"agent_step","run_id":1,"index":0,"step":{...},"status":"started"}
event: agent_tool    data: {"type":"agent_tool","run_id":1,"step_index":0,"tool":"calculator",
                            "arguments":{...},"status":"started"}
event: agent_tool    data: {"type":"agent_tool","run_id":1,"step_index":0,"tool":"calculator",
                            "status":"completed","result":5,"duration_ms":12}
event: agent_step    data: {"type":"agent_step","run_id":1,"index":0,"step":{...},
                            "status":"completed","output":"5"}
event: delta         data: {"type":"delta","content":"根据..."}     ← 复用聊天事件,可多次
event: usage         data: {"type":"usage","usage":{model,prompt_tokens,completion_tokens,total_tokens}}
event: done          data: {"type":"done","run_id":1,"status":"completed"}
```

失败时任意阶段可出现 `event: error, data: {"type":"error","message":"..."}`,随后流结束。前端 `consumeStream` 只识别 `delta`/`usage`/`error`/`done`,`agent_plan`/`agent_step`/`agent_tool` 作为结构化事件收集到 `agentEvents`,供计划/工具卡片面板渲染。

**前端接入**(`frontend/src`):

- `api/chat.ts`:`sendAgentMessage(sessionId, message, signal)` 发起 `POST /agent/stream`;`fetchAgentRun(runId)` 查询 trace;
- `stores/chat.ts`:`sendAgentMessageAction(text)` 消费 SSE 流,占位文案"正在规划任务...",非 delta/usage 事件push进 `agentEvents`;
- `types/index.ts`:`AgentPlanStep` / `AgentPlanEvent` / `AgentStepEvent` / `AgentToolEvent` / `AgentDoneEvent` / `AgentSSEEvent`(联合类型)/ `AgentRun` / `AgentTracePoint`。

---

## 7. 项目运行方式

### 7.1 环境准备

1. 复制 `.env.example` 为 `.env`,填写 `DEEPSEEK_API_KEY` 或 `OPENAI_API_KEY`(也可登录后在"个人中心"配置,个人设置优先);
2. 确认 `DATABASE_URL`(PostgreSQL)与 `REDIS_URL` 指向可连接的实例,Redis 需单独启动;
3. 安装依赖:`pip install -r requirements.txt`(虚拟环境 `.venv`)。

### 7.2 启动后端

项目根目录:

```powershell
.\.venv\Scripts\uvicorn.exe backend.app.main:app --reload
```

接口文档:`http://127.0.0.1:8000/docs`。agent 接口需先登录获取 token,请求带 `Authorization: Bearer <token>`。

### 7.3 启动前端

`frontend/` 目录:

```powershell
npm install
npm run dev
```

登录后选择会话,通过 `sendAgentMessageAction` 触发 agent 模式发送。

### 7.4 数据库迁移

agent 表由迁移 `a3b4c5d6e7f8_add_agent_tables` 创建。在 `backend/` 下执行:

```powershell
..\.venv\Scripts\alembic.exe upgrade head
```

> 注意:`backend/alembic.ini` 的 `sqlalchemy.url` 需与 `core.database.DATABASE_URL` 指向同一数据库。

### 7.5 运行示例与测试

```powershell
# agent 命令行示例(读 .env 的 Key,trace 打印到终端,不连数据库;backend/ 目录下)
..\.venv\Scripts\python.exe -m examples.agent_demo
..\.venv\Scripts\python.exe -m examples.agent_demo "查询北京今天的天气,然后把温度乘以 2"

# agent 框架单测(FakeClient + StubTracer,不依赖真实 LLM/数据库)
..\.venv\Scripts\python.exe tests\test_agent_framework.py

# 规划器集成测试(连接真实 LLM,需个人中心或 .env 配好 Key)
..\.venv\Scripts\python.exe tests\test_planner.py
```

---

## 8. 关键设计说明

1. **三阶段显式编排 vs 单轮 ReAct**:规划与执行解耦——计划先行可预览、可限制步数(`max_steps=6`);执行器拿到确定性步骤时直接调工具(快路径省 LLM 调用),不确定时才进入 LLM 工具循环(慢路径,`max_tool_turns=5` 轮,耗尽后无工具请求兜底),在成本与灵活性间取舍。

2. **事件流即协议**:主循环只 yield Pydantic 对象,不感知 HTTP/SSE;`AgentState` 快照夹在事件流末尾、由 service 层拦截,同一入口同时服务流式(`run_agent_stream`)与非流式(`run_agent`)两种消费方式。复用聊天流的 `delta`/`usage` 事件,前端渲染最终答案的代码零改动。

3. **Tracer 双实现与回调桥接**:`AgentTracer`(落库)与 `NullTracer`(空操作)接口对齐,框架层无 tracer 也能跑(测试、demo);工具调用过程经 `on_tool_call` / `on_tool_result` 回调桥接进 tracer——`point()` 落库、`emit()` 暂存,主循环在步骤边界 `drain_events()` 统一推送,避免工具事件与步骤事件交错乱序。

4. **工具层注册表模式**:工具模块只需提供 `TOOLS`(JSON Schema 给模型)与 `TOOL_REGISTRY`(name→函数给代码),在 `tools/__init__.py` 登记;`execute_tool_call` 按函数签名自动注入 `db` / `user_id` 上下文,工具错误统一转 JSON 回传模型让其自行修正。agent 与普通聊天共享全部 5 个工具。

5. **容错链路**:规划器对 `response_format` 不兼容自动降级重试、对 markdown 围栏与前后废话容错解析;步骤失败不中断整轮,结果以 failed 状态进入总结;service 层显式 try/except 兜底(Starlette 会静默吞掉流式生成器内的异常),失败补写 trace 与 run 状态后仍以 SSE error 事件收尾。

6. **可观测性**:每次运行产生完整 trace(`plan`/`step`/`llm`/`tool_call`/`finalize`/`error` 六类 stage,含输入输出、耗时、错误),按 `sequence` 排序还原时间线;`GET /agent/runs/{run_id}` 对外暴露,配合 token 计数可做成本核算与排错。

7. **资源与安全边界**:Redis 限流 200 次/小时/用户;运行记录按 `user_id` 租户隔离;`should_stop` 回调贯穿执行与总结阶段,支持用户中断且状态正确落为 `stopped`;停止/中断的响应头(`no-cache`/`keep-alive`/`X-Accel-Buffering: no`)防止反向代理缓冲 SSE。

# RAG 模块 Code Wiki

> 本文档对 AIChatPro 项目中的 **RAG（Retrieval-Augmented Generation，检索增强生成）** 模块进行结构化梳理，覆盖整体架构、模块职责、关键类与函数、依赖关系、数据模型以及项目运行方式。

---

## 目录

- [1. 项目整体架构](#1-项目整体架构)
  - [1.1 RAG 模块在系统中的位置](#11-rag-模块在系统中的位置)
  - [1.2 核心数据流](#12-核心数据流)
  - [1.3 目录结构](#13-目录结构)
- [2. 主要模块职责](#2-主要模块职责)
- [3. 数据模型](#3-数据模型)
- [4. 关键类与函数说明](#4-关键类与函数说明)
  - [4.1 loader.py — 文件加载](#41-loaderpy--文件加载)
  - [4.2 splitter.py — 文本切分](#42-splitterpy--文本切分)
  - [4.3 embedding.py — 向量化](#43-embeddingpy--向量化)
  - [4.4 retriever.py — 检索](#44-retrieverpy--检索)
  - [4.5 crud.py — 数据访问](#45-crudpy--数据访问)
  - [4.6 prompts.py — 上下文构造](#46-promptspy--上下文构造)
  - [4.7 service.py — 业务编排](#47-servicepy--业务编排)
  - [4.8 router.py — HTTP 接口](#48-routerpy--http-接口)
  - [4.9 schemas.py — 响应模型](#49-schemaspy--响应模型)
- [5. 依赖关系](#5-依赖关系)
  - [5.1 模块内部依赖](#51-模块内部依赖)
  - [5.2 外部依赖](#52-外部依赖)
- [6. 配置项](#6-配置项)
- [7. 项目运行方式](#7-项目运行方式)
- [8. 关键设计说明](#8-关键设计说明)

---

## 1. 项目整体架构

### 1.1 RAG 模块在系统中的位置

AIChatPro 是一个本地 AI 聊天应用，采用前后端分离架构。RAG 模块作为后端的一个独立子包，挂载在 `backend/app/rag/` 下，承担"知识库文档管理 + 检索增强生成"职责，为聊天流程注入外部知识上下文。

```text
┌──────────────┐   HTTP   ┌──────────────────────────────────────────┐
│  前端 Vue    │ ───────► │  FastAPI 后端 (backend/app)              │
│  /ChatView   │          │                                          │
└──────────────┘          │  routers/chat.py  ──► services/messages  │
                          │                            │              │
                          │            augment_messages_with_rag ◄────┤ RAG 模块
                          │                            │              │ (backend/app/rag)
                          │                       services/llm        │
                          │                            │              │
                          │                  DeepSeek / OpenAI        │
                          └──────────────────────────────────────────┘
                                       │                │
                              ┌────────▼───────┐  ┌─────▼─────┐
                              │ PostgreSQL 17   │  │  Redis    │
                              │ + pgvector 扩展 │  │ (缓存/会话)│
                              └────────────────┘  └───────────┘
```

RAG 模块对外提供两条通路：

1. **HTTP 接口通路**（`router.py`）：文档上传、文档列表、文档删除、知识检索，供前端/管理后台直接调用。
2. **聊天增强通路**（`service.augment_messages_with_rag`）：在普通聊天与流式聊天发送消息前，对用户消息进行检索，把命中的知识片段拼成 system 消息注入到上下文中。

### 1.2 核心数据流

**文档入库（索引）流程：**

```text
UploadFile
  │
  ▼
service.upload_document_service()
  ├─ 读取文件字节，计算 sha256 文档指纹
  ├─ 落盘到 rag_upload_dir（uuid_安全文件名）
  ├─ crud.create_document()  → status="pending" 写入 rag_documents
  └─ index_document()
       ├─ loader.load_text_from_file()   读取文本 + 编码探测
       ├─ splitter.split_text()          段落聚合 + 滑窗切分 → TextChunk[]
       ├─ embedding.embed_texts()        OpenAI Embeddings → 向量[]
       └─ crud.replace_document_chunks() 写入 rag_chunks + rag_chunk_embeddings
                                        → status="ready"
       （任意环节失败 → mark_document_failed() → status="failed"）
```

**检索增强生成流程：**

```text
用户消息
  │
  ▼
services/messages.send_message_stream_service()
  ├─ 写入用户消息、加载历史
  ├─ augment_messages_with_rag(db, user_id, message, history)
  │     ├─ 若 rag_enabled=False → 直接返回原 messages
  │     ├─ retriever.retrieve_relevant_chunks()
  │     │     ├─ embedding.embed_query()        查询向量化
  │     │     └─ pgvector cosine_distance 检索 top_k
  │     ├─ prompts.build_context_message()      拼 system 上下文（带引用编号）
  │     └─ 返回 [context_message, *messages]    （检索失败不阻断聊天）
  └─ stream_ai_reply() → DeepSeek/OpenAI 流式回复
```

### 1.3 目录结构

```text
backend/app/rag/
├── __init__.py        # 包入口，re-export 三个 ORM 模型
├── models.py          # ORM 模型 re-export（实际定义在 app/models.py）
├── schemas.py         # Pydantic 响应模型
├── loader.py          # 文件加载与编码探测
├── splitter.py        # 文本切分（段落聚合 + 滑窗）
├── embedding.py       # OpenAI Embeddings 调用
├── retriever.py       # pgvector 向量检索
├── crud.py            # 数据库 CRUD
├── prompts.py         # 上下文消息构造
├── service.py         # 业务编排层（核心）
└── router.py          # FastAPI 路由
```

相关支撑文件：

```text
backend/app/models.py                          # RagDocument / RagChunk / RagChunkEmbedding ORM 定义
backend/app/core/config.py                     # RAG_* 配置项
backend/app/services/messages.py               # 聊天流程接入 RAG 的入口
backend/alembic/versions/d3b2a1f7c9e8_add_rag_tables.py  # 建表迁移（含 pgvector 扩展）
```

---

## 2. 主要模块职责

| 文件 | 职责 | 关键导出 |
|------|------|----------|
| `router.py` | HTTP 路由层，定义 4 个 REST 接口，只做依赖注入与参数校验 | `router` |
| `service.py` | 业务编排层，串联上传/索引/删除/检索/聊天增强全流程 | `upload_document_service`、`index_document`、`augment_messages_with_rag` 等 |
| `crud.py` | 数据访问层，封装三张表的增删查改 | `create_document`、`replace_document_chunks` 等 |
| `loader.py` | 文件加载，校验扩展名、探测编码、清洗文本 | `load_text_from_file`、`SUPPORTED_SUFFIXES` |
| `splitter.py` | 文本切分，按段落聚合、超长文本滑窗、生成内容哈希 | `split_text`、`TextChunk` |
| `embedding.py` | 向量化，调用 OpenAI Embeddings，校验维度 | `embed_texts`、`embed_query` |
| `retriever.py` | 向量检索，基于 pgvector 余弦距离取 top_k | `retrieve_relevant_chunks`、`RetrievalHit` |
| `prompts.py` | 上下文构造，把命中片段拼成带引用编号的 system 消息 | `build_context_message` |
| `schemas.py` | Pydantic 响应模型，约束接口输出 | `RagUploadResponse`、`RagSearchResponse` 等 |
| `models.py` | ORM 模型 re-export，统一导入入口 | `RagDocument`、`RagChunk`、`RagChunkEmbedding` |

---

## 3. 数据模型

三张表通过 `backend/alembic/versions/d3b2a1f7c9e8_add_rag_tables.py` 建立，迁移时先执行 `CREATE EXTENSION IF NOT EXISTS vector` 启用 pgvector。表之间通过 `ON DELETE CASCADE` 实现级联删除。

```text
rag_documents (1) ──┐
                    │  document_id (FK, CASCADE)
                    ▼
              rag_chunks (1) ──┐
                               │  chunk_id (FK, CASCADE, UNIQUE)
                               ▼
                       rag_chunk_embeddings (1)
```

### `RagDocument`（rag_documents — 文档元数据）

定义位置：`backend/app/models.py`

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | Integer PK | 自增主键 |
| `user_id` | FK → users.id | 所属用户（知识库按用户隔离） |
| `filename` | String(255) | 安全清洗后的文件名 |
| `storage_path` | String(500) | 落盘绝对/相对路径 |
| `mime_type` | String(120) | 上传时 content_type，可空 |
| `file_size` | Integer | 原始字节数 |
| `status` | String(30) | `pending` / `ready` / `failed` |
| `doc_hash` | String(64) | 文件内容 sha256，用于去重/校验 |
| `chunk_count` | Integer | 切片数量，索引成功后更新 |
| `embedding_model` | String(120) | 实际使用的 embedding 模型名 |
| `error_message` | Text | 失败原因（最长 2000 字符截断） |
| `created_at` / `updated_at` | DateTime | 时间戳 |

索引：`ix_rag_documents_user_status_created (user_id, status, created_at)`，加速"按用户列出 ready 文档"。

### `RagChunk`（rag_chunks — 文本切片）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | Integer PK | 自增主键 |
| `document_id` | FK → rag_documents.id (CASCADE) | 所属文档 |
| `chunk_index` | Integer | 切片序号 |
| `content` | Text | 切片文本 |
| `token_count` | Integer | 粗略 token 估算（len//4） |
| `page_no` | Integer | 预留页码字段，当前未使用 |
| `section_title` | String(255) | 预留章节标题，当前未使用 |
| `content_hash` | String(64) | 切片内容 sha256 |
| `created_at` | DateTime | 创建时间 |

索引：`ix_rag_chunks_document_index (document_id, chunk_index)`。

### `RagChunkEmbedding`（rag_chunk_embeddings — 向量存储）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | Integer PK | 自增主键 |
| `chunk_id` | FK → rag_chunks.id (CASCADE), UNIQUE | 一对一关联切片 |
| `model` | String(120) | 生成向量的模型名 |
| `dimension` | Integer | 向量维度（默认 1536） |
| `embedding` | `Vector(rag_embedding_dimension)` | pgvector 向量列 |
| `created_at` | DateTime | 创建时间 |

索引：`ix_rag_chunk_embeddings_embedding_ivfflat`，使用 `ivfflat` 算法、`vector_cosine_ops` 操作符、`lists=100`，支持余弦相似度近似最近邻检索。

> **注意**：`embedding` 列维度由 `settings.rag_embedding_dimension` 决定，迁移脚本硬编码为 `Vector(1536)`。更换 embedding 模型时维度必须与配置一致，否则 `embedding.py` 会在运行时抛出 `BusinessError`。

---

## 4. 关键类与函数说明

### 4.1 loader.py — 文件加载

**`SUPPORTED_SUFFIXES`**：`{".txt", ".md", ".markdown", ".csv", ".json", ".log"}`，RAG 仅支持纯文本类文件。

**`load_text_from_file(path, filename) -> str`**

- 按文件名后缀校验是否受支持，不支持抛 `BusinessError`。
- 读取原始字节，依次尝试 `utf-8` → `utf-8-sig` → `gb18030` 解码，全部失败则报错。
- 统一换行符为 `\n` 并 `strip()`，空内容抛 `BusinessError("文档内容为空")`。

### 4.2 splitter.py — 文本切分

**`TextChunk`**（`@dataclass(frozen=True)`）：切片结果，包含 `index`、`content`、`token_count`、`content_hash`。

**`split_text(text, chunk_size, overlap) -> list[TextChunk]`**

切分策略（两层）：

1. **段落聚合**：按 `\n\n` 切段落并 `strip()`，逐段尝试累加到当前 chunk；累加后超过 `chunk_size` 则封存当前 chunk 并开启新 chunk。
2. **超长段落滑窗**：单段超过 `chunk_size` 时调用 `_split_long_text()`，以 `step = chunk_size - overlap` 滑窗切分，保证跨切片上下文连续。

每个 chunk 用 `sha256(content)` 生成 `content_hash`，`token_count` 由 `_estimate_tokens()` 粗估（`max(1, len(text) // 4)`），仅用于记录与限流，真实 token 由模型返回。

### 4.3 embedding.py — 向量化

**`embed_texts(texts, user_id, db) -> list[list[float]]`**

- 空列表直接返回 `[]`。
- 通过 `_get_embedding_api_key()` 解析 API Key：**优先取用户个人设置中 provider=openai 的 api_key**，否则回退到全局 `settings.openai_api_key`。
- 使用 `OpenAI(api_key, base_url=settings.openai_base_url)` 调用 `embeddings.create(model=settings.rag_embedding_model, input=texts)`。
- 逐向量校验维度是否等于 `settings.rag_embedding_dimension`，不一致抛 `BusinessError`。
- `openai` 包未安装时 `OpenAI = None`，调用时抛 `BusinessError("RAG 向量化需要配置 OpenAI API Key")`。

**`embed_query(query, user_id, db) -> list[float]`**：单条查询的便捷封装，等价于 `embed_texts([query], ...)[0]`。

### 4.4 retriever.py — 检索

**`RetrievalHit`**（`@dataclass(frozen=True)`）：检索命中结果，含 `document_id`、`chunk_id`、`filename`、`content`、`score`。

**`retrieve_relevant_chunks(db, user_id, query, top_k) -> list[RetrievalHit]`**

- 调用 `embed_query()` 把查询文本转向量。
- 用 pgvector 的 `RagChunkEmbedding.embedding.cosine_distance(query_vector)` 计算余弦距离。
- 三表 JOIN：`rag_documents` ↔ `rag_chunks` ↔ `rag_chunk_embeddings`，过滤 `user_id` 与 `status="ready"`，按距离升序取 `top_k`。
- 距离转换为相似度分数：`score = max(0.0, 1.0 - distance)`。

### 4.5 crud.py — 数据访问

| 函数 | 作用 |
|------|------|
| `create_document(db, user_id, filename, storage_path, mime_type, file_size, doc_hash)` | 创建文档记录，初始 `status="pending"` |
| `list_documents(db, user_id)` | 按 `created_at desc, id desc` 列出用户全部文档 |
| `get_document_by_user(db, document_id, user_id)` | 取单条文档并校验归属（用户隔离） |
| `delete_document(db, document)` | 删除文档（级联删除 chunks/embeddings） |
| `replace_document_chunks(db, document, chunks, embeddings, model, dimension)` | **重建索引核心**：先删旧 chunks，再逐条插入 chunk + embedding，最后置 `status="ready"`、更新 `chunk_count`/`embedding_model` |
| `mark_document_failed(db, document, message)` | 置 `status="failed"`，错误信息截断 2000 字符 |

> `replace_document_chunks` 通过 `db.flush()` 拿到 chunk 自增 id 后再写对应 embedding，保证外键正确；`zip(chunks, embeddings, strict=True)` 确保两者长度严格一致。

### 4.6 prompts.py — 上下文构造

**`build_context_message(hits, max_chars) -> dict | None`**

- 无命中返回 `None`。
- 逐条消费命中片段，按 `max_chars` 预算截断，格式：`[index] source=filename chunk_id=N\n<content>`。
- 多片段用 `\n\n` 拼接，外层包裹 system 角色指令：
  > "Use the following retrieved knowledge only when it is relevant. If it does not answer the user, say so and answer from general knowledge. Do not invent citations."
- 返回 `{"role": "system", "content": ...}`，无可用内容时返回 `None`。

### 4.7 service.py — 业务编排

业务层核心，所有对外能力在此汇聚。

**`upload_document_service(db, user, file)`** — 上传并立即索引

- 读取字节、空校验、计算 sha256。
- `_resolve_upload_dir()` 解析落盘目录（相对路径基于 `PROJECT_ROOT`），`_safe_filename()` 清洗文件名（取 basename、去 NULL 字符）。
- 落盘文件名加 `uuid4().hex` 前缀防冲突。
- `create_document()` 落库后立即 `index_document()`；失败时 `mark_document_failed()`，**不向上抛异常**，而是把失败信息写进 `error_message` 返回给前端。

**`index_document(db, user_id, document_id)`** — 索引主流程

串联 `load_text_from_file` → `split_text` → `embed_texts`（批量）→ `replace_document_chunks`。批量 embedding 减少网络往返，失败整篇标记 `failed`。

**`delete_document_service(db, user, document_id)`** — 删除

校验归属后删库（级联）+ 删磁盘文件（存在且是文件才 unlink）。

**`list_documents_service(db, user)`** — 列表

`_serialize_document()` 把 ORM 对象序列化为响应字典，`_format_dt()` 兼容 str/datetime。

**`search_documents_service(db, user, query)`** — 检索

`query.strip()` 空校验后调用 `retrieve_relevant_chunks(top_k=settings.rag_top_k)`，返回命中列表。

**`augment_messages_with_rag(db, user_id, user_message, messages)`** — 聊天增强（关键集成点）

- `rag_enabled=False` 直接返回原 `messages`。
- 检索过程 `try/except` 兜底：**检索失败不阻断基础聊天**，静默返回原 messages。
- `build_context_message()` 返回 `None`（无命中或超预算）时不注入。
- 命中时返回 `[context_message, *messages]`，system 上下文置于消息列表最前。

### 4.8 router.py — HTTP 接口

路由前缀 `/rag`，tag `RAG`，全部依赖 `get_current_user` 鉴权。

| 方法 | 路径 | 函数 | 说明 |
|------|------|------|------|
| GET | `/rag/documents` | `list_documents` | 列出当前用户文档 |
| POST | `/rag/upload` | `upload_document` | 上传文件（`UploadFile`） |
| DELETE | `/rag/documents/{document_id}` | `delete_document` | 删除文档 |
| GET | `/rag/search` | `search_documents` | 检索，`q` 参数 1~1000 字符 |

`CurrentUser` 与 `Database` 通过 `Annotated[..., Depends(...)]` 注入。路由层不含业务逻辑，全部委托给 `service.py`。

### 4.9 schemas.py — 响应模型

| 模型 | 用途 |
|------|------|
| `RagDocumentResponse` | 单文档详情（id/filename/status/chunk_count/error_message 等） |
| `RagDocumentListResponse` | `{success, documents[]}` |
| `RagUploadResponse` | `{success, document}` |
| `RagSearchHit` | 单条检索命中（document_id/chunk_id/filename/content/score） |
| `RagSearchResponse` | `{success, hits[]}` |

---

## 5. 依赖关系

### 5.1 模块内部依赖

```text
router.py
  └─► service.py
        ├─► crud.py ─► models (RagDocument/RagChunk/RagChunkEmbedding)
        ├─► loader.py
        ├─► splitter.py
        ├─► embedding.py ─► core.config / crud.get_user_settings
        ├─► retriever.py ─► embedding.embed_query / models
        └─► prompts.py ──► retriever.RetrievalHit

schemas.py ◄── router.py（响应模型）
```

外部接入点：

- `backend/app/main.py`：`app.include_router(rag_router)` 注册路由。
- `backend/app/services/messages.py`：在 `send_message_service`、`update_message_service`（编辑重发）、`send_message_stream_service`（流式）三处调用 `augment_messages_with_rag`。

### 5.2 外部依赖

来自 `requirements.txt`：

| 依赖 | 版本 | RAG 中的用途 |
|------|------|--------------|
| `fastapi` | 0.128.8 | 路由、依赖注入、`UploadFile` |
| `sqlalchemy` | 2.0.51 | ORM、查询构造、Session |
| `pgvector` | 0.4.2 | `Vector` 列类型、`cosine_distance` 检索 |
| `psycopg[binary]` | 3.2.9 | PostgreSQL 驱动 |
| `alembic` | 1.18.5 | 数据库迁移 |
| `openai` | 1.30.5 | Embeddings API 调用 |
| `pydantic` | 2.13.4 | 响应模型校验 |
| `pydantic-settings` | 2.10.1 | `Settings` 配置加载 |

外部服务依赖：

- **PostgreSQL 17 + pgvector 扩展**：存储向量并执行 ANN 检索。
- **OpenAI 兼容 Embeddings 服务**：`settings.openai_base_url` 指向，默认 `text-embedding-3-small`（1536 维）。
- **Redis**：非 RAG 直接依赖，但聊天流程（接入 RAG 的入口）依赖 Redis 做会话/限流。

---

## 6. 配置项

RAG 配置集中在 `backend/app/core/config.py` 的 `Settings` 类，通过 `.env`（项目根或 backend 目录）注入。`.env.example` 提供模板。

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `RAG_ENABLED` | `true` | 全局开关，关闭后聊天不注入 RAG 上下文 |
| `RAG_UPLOAD_DIR` | `backend/uploads/rag` | 文件落盘目录，相对路径基于 `PROJECT_ROOT` |
| `RAG_EMBEDDING_MODEL` | `text-embedding-3-small` | OpenAI Embeddings 模型名 |
| `RAG_EMBEDDING_DIMENSION` | `1536` | 向量维度，**必须与模型实际维度和迁移脚本 Vector(n) 一致** |
| `RAG_TOP_K` | `5` | 检索返回片段数 |
| `RAG_CHUNK_SIZE` | `900` | 切片目标长度（字符） |
| `RAG_CHUNK_OVERLAP` | `150` | 滑窗重叠（字符） |
| `RAG_MAX_CONTEXT_CHARS` | `5000` | 注入上下文的最大字符预算 |

API Key 解析优先级（`embedding._get_embedding_api_key`）：

1. 用户个人设置中 `provider == "openai"` 的 `api_key`（存于 `user_settings` 表，Redis 缓存 `user:settings:{user_id}`）。
2. 全局 `OPENAI_API_KEY`。

---

## 7. 项目运行方式

### 7.1 环境准备

1. **PostgreSQL + pgvector**：`docker-compose.yml` 提供 `postgres:17`，但 RAG 需要 pgvector 扩展。生产/开发建议使用 `pgvector/pgvector:pg17` 镜像，或在容器内手动执行 `CREATE EXTENSION vector`（迁移脚本会执行 `CREATE EXTENSION IF NOT EXISTS vector`，但要求扩展已安装）。

   启动基础设施：

   ```powershell
   docker compose up -d --wait
   ```

   `--wait` 等待 healthcheck 通过（PostgreSQL 用 `pg_isready`，Redis 用 `redis-cli ping`）。

2. **Python 虚拟环境**（项目根目录）：

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

3. **环境变量**：复制 `.env.example` 为 `.env`，填入 `OPENAI_API_KEY`（或让用户在个人中心保存），确认 `DATABASE_URL` 指向 PostgreSQL。

### 7.2 数据库迁移

RAG 表通过 Alembic 迁移建立（迁移文件 `d3b2a1f7c9e8_add_rag_tables.py`，前置迁移 `c4e7d9a8f2b1_add_rbac_tables.py`）：

```powershell
.\.venv\Scripts\alembic.exe -c backend\alembic.ini upgrade head
```

迁移会：启用 vector 扩展 → 建 3 张表 → 创建 3 个索引（含 ivfflat 向量索引）。

### 7.3 启动后端

```powershell
.\.venv\Scripts\uvicorn.exe backend.app.main:app --reload
```

- 接口文档：`http://127.0.0.1:8000/docs`
- RAG 接口前缀：`http://127.0.0.1:8000/rag`

### 7.4 启动前端

前端为 Vue 3 + Vite 工程（`frontend/`），通过 `frontend/src/api/` 调用后端。

```powershell
cd frontend
npm install
npm run dev
```

### 7.5 验证 RAG 流程

1. 登录后在个人中心配置 OpenAI API Key。
2. `POST /rag/upload` 上传 `.txt`/`.md`/`.csv`/`.json`/`.log` 文件，等待 `status` 变为 `ready`。
3. `GET /rag/search?q=关键词` 验证检索命中。
4. 在聊天中发送相关问题，观察 AI 回复是否引用知识库内容（system 上下文已注入）。

---

## 8. 关键设计说明

1. **按用户隔离的知识库**：所有检索与列表查询都带 `user_id` 过滤，`get_document_by_user` 校验归属，避免越权访问他人文档。

2. **失败不阻断上传响应**：`upload_document_service` 捕获索引异常并写入 `error_message`，HTTP 仍返回 200 与 `status="failed"`，便于前端展示失败原因并重试。

3. **RAG 增强是"尽力而为"**：`augment_messages_with_rag` 对检索过程 `try/except` 兜底，检索失败、无命中、未启用都直接返回原 messages，保证基础聊天可用性。

4. **维度强校验**：`embed_texts` 逐向量校验维度，避免维度不匹配污染向量索引；迁移脚本与配置维度需保持一致。

5. **级联删除**：`rag_chunks.document_id` 与 `rag_chunk_embeddings.chunk_id` 均 `ON DELETE CASCADE`，删文档时自动清理切片与向量，service 层额外删除磁盘文件。

6. **批量 embedding**：`index_document` 一次性对全部 chunks 调用 `embed_texts`，减少网络往返；任一片段失败则整篇标记 `failed`（atomic 语义由业务层保证，非数据库事务）。

7. **安全清洗**：`_safe_filename` 取 basename 并去 NULL 字符，防止路径穿越；落盘文件名加 uuid 前缀防冲突与覆盖。

8. **ivfflat 索引**：向量检索使用 PostgreSQL pgvector 的 ivfflat 近似最近邻索引（`lists=100`，余弦距离），适合中规模知识库；超大规模场景需调整 `lists` 或考虑 HNSW。

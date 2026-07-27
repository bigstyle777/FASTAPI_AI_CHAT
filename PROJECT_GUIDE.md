# AI Chat Pro 项目导读

这个项目是一个本地 AI 聊天应用。前端负责页面交互，后端提供 FastAPI 接口，SQLite 负责持久化数据，Redis 负责验证码、登录态和缓存，AI 回复由 DeepSeek 或 OpenAI 兼容接口生成。

```text
frontend 页面
  -> fetch 请求
backend FastAPI 接口
  -> routers 接收 HTTP 请求
  -> services 编排业务
  -> crud 读写 SQLite
  -> core 管理基础设施
Redis + SQLite + DeepSeek/OpenAI
```

## 目录结构

```text
frontend/
  index.html        页面结构，登录页、聊天页、个人中心入口都在这里
  style.css         页面样式，控制布局、按钮、会话列表、消息区和个人中心
  app.js            浏览器端逻辑，负责登录、注册、验证码、发消息和调用后端接口

backend/app/
  main.py           FastAPI 应用入口，注册路由、中间件、异常处理，并初始化数据库
  routers/          HTTP 路由层，只定义 URL、请求参数、依赖注入和响应模型
    users.py        用户、验证码、登录、登出、个人信息、个人设置接口
    chat.py         会话列表、创建会话、消息列表、普通聊天、流式聊天接口
  services.py       业务流程层，编排认证、验证码、聊天、用户设置和数据库操作
  crud.py           SQLite 数据访问层，封装用户、会话、消息、设置的增删查改
  models.py         SQLAlchemy ORM 模型，定义 users、chat_sessions、messages、user_settings
  schemas.py        Pydantic 请求和响应模型
  auth.py           JWT、密码哈希、登录态校验、用户资料缓存
  ai.py             DeepSeek/OpenAI 客户端创建、AI 调用、流式输出和兜底回复
  exceptions.py     业务异常类型
  core/
    database.py     SQLite 路径、SQLAlchemy engine、SessionLocal、Base、get_db、init_db
    redis.py        Redis 连接、JSON 读写 helper 和不可用异常
    config.py       基于 pydantic-settings 的配置中心，集中管理 .env、默认值、类型转换和数据库/Redis/JWT/AI 配置
    security.py     安全工具，封装密码哈希、密码验证、JWT 创建/解码和 token 摘要
```

## 配置和环境变量

环境变量示例在 `.env.example`。目前代码读取配置的方式是：

```text
.env.example 只提供模板
.env          本地真实配置，已被 .gitignore 忽略
core/config.py 通过 pydantic-settings 读取 backend/.env 和项目根目录 .env
core/config.py 中的 settings 是统一配置对象
ai.py、auth.py、services.py、core/redis.py、core/database.py 都从 settings 读取配置
```

常用配置：

```text
DEEPSEEK_API_KEY=
OPENAI_API_KEY=
OPENAI_BASE_URL=
OPENAI_MODEL=gpt-4o-mini
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
JWT_SECRET_KEY=change-me
JWT_ALGORITHM=HS256
REDIS_URL=redis://127.0.0.1:6379/0
ACCESS_TOKEN_TTL_SECONDS=2592000
CAPTCHA_TTL_SECONDS=300
USER_CACHE_TTL_SECONDS=86400
USER_SETTINGS_CACHE_TTL_SECONDS=3600
DATABASE_PATH=backend/data/chat.db
DATABASE_URL=
```

`DATABASE_URL` 存在时优先生效；否则使用 `DATABASE_PATH` 生成 SQLite URL。相对 `DATABASE_PATH` 会按项目根目录解析。

## Redis 怎么用

Redis 封装在 `backend/app/core/redis.py`。

`get_redis()` 使用 redis 库的 `Redis.from_url()` 创建原生 Redis 连接：

```text
REDIS_URL 默认 redis://127.0.0.1:6379/0
decode_responses=True
socket_connect_timeout=2
socket_timeout=2
```

`core/redis.py` 没有自定义 Redis client 类，只提供少量函数：

```text
redis_set(key, value, ttl)
redis_get(key)
redis_delete(*keys)
redis_set_json(key, value, ttl)
redis_get_json(key)
```

Redis 出错会抛 `RedisUnavailableError`，`main.py` 会把它转换成 HTTP 503。

当前 Redis key 设计：

```text
auth:captcha:{captcha_id}       登录验证码，默认 300 秒，验证成功后删除
auth:token:{sha256(token)}      登录态，默认 30 天，登出时删除
user:profile:{user_id}          用户资料缓存，默认 1 天
user:settings:{user_id}         用户 AI 设置缓存，默认 1 小时
```

Redis 不保存聊天记录。聊天会话、消息、用户和个人设置仍然持久化在 SQLite。

## 数据库

运行时代码的 SQLite 配置来自 `core/config.py`，由 `backend/app/core/database.py` 使用：

```text
DB_PATH = backend/data/chat.db
DATABASE_URL = sqlite:///.../backend/data/chat.db
```

`main.py` 导入 `init_db()` 后会执行 `Base.metadata.create_all(bind=engine)`，确保本地开发时表存在。

Alembic 迁移文件位于 `backend/alembic/`。执行迁移相关命令前，需要确认 `backend/alembic.ini` 里的 `sqlalchemy.url` 和 `core.database.DATABASE_URL` 指向同一个数据库文件。

## 一次登录发生了什么

1. 前端点击“登录”，触发 `frontend/app.js` 的 `handleLogin()`。
2. 前端先通过 `POST /users/captcha` 获取验证码。
3. 用户提交用户名、密码、验证码后，请求 `POST /users/login`。
4. `routers/users.py` 把请求交给 `services.login_user()`。
5. `services._verify_captcha()` 从 Redis 读取验证码并校验，成功后删除验证码 key。
6. `crud.get_user_by_username()` 查询用户。
7. `auth.verify_password()` 校验密码。
8. `auth.create_login_session()` 生成 JWT，并把 token 登录态写入 Redis。
9. 前端把返回的 token 存到 `localStorage`，后续请求带 `Authorization: Bearer <token>`。

## 一次鉴权发生了什么

1. 路由通过 `Depends(get_current_user)` 要求登录。
2. `auth.get_current_token()` 从 `Authorization` header 取出 token。
3. `auth.decode_token()` 校验 JWT 签名和过期时间。
4. `auth.get_current_user()` 用 token 的 SHA-256 摘要拼 Redis key。
5. Redis 中存在登录态，并且 user_id 与 JWT payload 一致，请求才会继续。

所以这个项目不是只依赖 JWT。JWT 有效但 Redis 登录态不存在时，请求仍会返回 401。

## 一次发消息发生了什么

1. 前端点击“发送”，触发 `frontend/app.js` 的发送逻辑。
2. 前端请求 `POST /chat/stream`。
3. `routers/chat.py` 先通过 `get_current_user` 做登录校验。
4. `services.send_message_stream_service()` 校验会话归属。
5. 用户消息写入 SQLite，并更新会话的 `last_message` 和 `updated_at`。
6. `ai.chat_with_ai_stream()` 调用 DeepSeek/OpenAI，并逐块 yield 给前端。
7. 后端累积完整 AI 回复，流结束后写入 SQLite。

## 本地运行

在项目根目录运行后端：

```powershell
.\.venv\Scripts\uvicorn.exe backend.app.main:app --reload
```

打开接口文档：

```text
http://127.0.0.1:8000/docs
```

前端可以直接用浏览器打开：

```text
C:\Users\bigstyle\AIChatPro\frontend\index.html
```

Redis 需要单独启动，并确保 `REDIS_URL` 指向可连接的 Redis 实例。

## API Key

项目支持两种 API Key 来源：

1. 在 `.env` 中配置 `DEEPSEEK_API_KEY` 或 `OPENAI_API_KEY`。
2. 登录后进入“个人中心”，填写 API Key 和 provider 后保存。

个人中心保存的 API Key 会写入 SQLite，并通过 Redis 缓存 `user:settings:{user_id}`。

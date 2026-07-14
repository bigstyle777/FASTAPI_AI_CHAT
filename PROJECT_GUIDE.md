# AI Chat Pro 项目导读

这个项目是一个本地聊天应用，分成三层：

```text
frontend 页面
  ↓ fetch 请求
backend FastAPI 接口
  ↓ 调业务、查数据库、调 AI
SQLite 数据库 + DeepSeek/OpenAI
```

## 目录怎么读

```text
frontend/
  index.html   页面结构，按钮和输入框都在这里
  style.css    页面样式，控制登录页、侧边栏、聊天区的外观
  app.js       浏览器里的业务逻辑，负责登录、注册、发消息、调用后端接口

backend/app/
  main.py      FastAPI 入口，初始化应用、数据库、路由和异常处理
  routers/     接口入口，只定义 URL 和参数
  services.py  业务流程，比如注册、登录、创建会话、发送消息
  crud.py      SQLite 增删查改
  database.py  数据库路径和表结构初始化
  auth.py      密码加密、密码验证、JWT 登录状态
  ai.py        调用 DeepSeek/OpenAI
  schemas.py   请求和响应的数据格式
```

## 一次登录发生了什么

1. 页面点击“登录”，触发 `frontend/app.js` 的 `handleLogin()`。
2. 前端请求 `POST /users/login`。
3. `backend/app/routers/users.py` 接住请求。
4. `backend/app/services.py` 的 `login_user()` 查用户并验证密码。
5. `backend/app/auth.py` 生成 JWT token。
6. 前端把 token 存到浏览器 `localStorage`。

## 一次发消息发生了什么

1. 页面点击“发送”，触发 `frontend/app.js` 的 `sendMessage()`。
2. 前端请求 `POST /chat/stream`。
3. 后端先保存用户消息到 SQLite。
4. `backend/app/ai.py` 调用 AI 接口并流式返回内容。
5. 前端一边接收，一边把 AI 回复显示到聊天窗口。
6. 后端最后把 AI 回复保存到 SQLite。

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

## API Key

项目支持两种配置方式：

1. 在 `.env` 里配置 `DEEPSEEK_API_KEY` 或 `OPENAI_API_KEY`。
2. 登录后进入“个人中心”，填入 API Key 并保存。

个人中心保存的 API Key 会写入本地 SQLite 数据库。

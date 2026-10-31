# 生产部署 Checklist

部署到生产环境前逐项核对。本地开发可不勾（dev 默认宽松），但上线必须全绿。

## .env 必配项

- [ ] **ENVIRONMENT=production**
  触发 JWT 弱密钥拒绝启动、cookie secure 标志等生产行为。忘了设等于裸奔。
- [ ] **JWT_SECRET_KEY** = 强随机 32+ 字符
  生成：`python -c "import secrets; print(secrets.token_urlsafe(48))"`
  生产下默认值 `myselectkey` 或长度不足 32 会直接拒绝启动。
- [ ] **BOOTSTRAP_ADMIN_PASSWORD** 改掉默认 `admin123456`
  首次启动会按此密码创建 admin 账号；之后改密码不会被重启覆盖。
  忘记密码的恢复方式：改 .env 的用户名（或删库重建），不会自动重置。
- [ ] **DATA_ENCRYPTION_KEY** = 强随机
  用户 API Key 落库加密用。未配置则明文存储（备份泄露即泄露所有用户 Key）。
  生成：`python -c "import secrets; print(secrets.token_urlsafe(32))"`
  ⚠️ 改密钥后旧密文无法解密（用户需重新填 Key）。
- [ ] **POSTGRES_PASSWORD / REDIS_PASSWORD** = 强密码
  docker-compose 用 `${VAR:?}` 强制要求，不设会启动失败。
- [ ] **CORS_ALLOWED_ORIGINS** 收窄到实际前端域名
  不要保留开发用的 localhost，生产前端经 nginx 同源反代时甚至可设为空。

## 可选但建议

- [ ] **METRICS_TOKEN** = 随机串
  `/metrics` 未配 token 时只允许回环访问；公网抓取需配 token 并带
  `Authorization: Bearer <token>`（或 `?token=`）。
- [ ] **HEALTH_EXPOSE_DETAILS=false**（默认即 false）
  排障时临时设 true 看 DB/Redis 异常原文，平时保持 false。
- [ ] **SERVER_API_KEY_FALLBACK_ENABLED**
  - 公开注册站点：保持 false（默认），用户没配 Key 时不能用服务器额度
  - 自用/内网：可设 true，省去每人配 Key 的麻烦
- [ ] **TRUSTED_PROXY_IPS** = 反代 IP
  仅当直连方在该名单内才采信 `X-Forwarded-For`（限流取真实 IP 用）。
  例：`TRUSTED_PROXY_IPS=127.0.0.1,10.0.0.2`

## secrets/ 目录（容器部署）

API 密钥走文件挂载，不出现在环境变量（`docker inspect` 不可见）：

```bash
mkdir secrets
echo "sk-..." > secrets/deepseek_api_key
echo "tvly-..." > secrets/tavily_api_key
echo "sk-..." > secrets/rag_embedding_api_key
```

容器内由 `*_KEY_FILE` 变量指向 `/run/secrets/<name>`。`secrets/` 已在
`.gitignore` / `.dockerignore`，不会进仓库或镜像。

## HTTPS

- cookie 在生产走 `secure`（自动按 ENVIRONMENT=production 开启），HTTP 下浏览器不会发
- HTTPS 在 nginx/反代层终止，backend 本身可走 HTTP
- 没 HTTPS 的环境不要设 ENVIRONMENT=production，否则登录 cookie 发不出来

## 首次启动后

- [ ] 登录 admin 账号，确认能进 `/admin`
- [ ] 检查向量索引存在（RAG 检索否则全表扫描）：
  ```sql
  \d ix_memories_embedding_ivfflat
  \d ix_rag_chunk_embeddings_embedding_ivfflat
  ```
  迁移 `e79946421824_ensure_vector_indexes` 会幂等补建。
- [ ] 跑一次 `alembic check`，应输出 `No new upgrade operations detected`
- [ ] 确认限流生效：连续 10 次登录失败应返回 429

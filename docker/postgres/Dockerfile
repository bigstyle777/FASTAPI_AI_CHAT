# 基于官方 postgres:17.4，预装 pgvector 扩展
# 这样 RAG 的 vector 列与 ivfflat 索引在容器重建后依然可用
FROM postgres:17.4

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-17-pgvector \
    && rm -rf /var/lib/apt/lists/*

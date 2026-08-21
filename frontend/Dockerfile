# ---------- 构建阶段：Vite 打包 ----------
# 构建上下文为项目根目录（见 docker-compose.yml 的 build.context）
# node 24 + npm 11 与本地开发环境及 package-lock.json 的生成版本一致
FROM node:24.9.0-alpine AS build

WORKDIR /app

# 先装依赖充分利用层缓存（node_modules 已被根 .dockerignore 排除，不会覆盖）
# --legacy-peer-deps：eslint-plugin-oxlint@1.73 的 peer 依赖 oxlint@~1.73
# 与项目的 oxlint@~1.74 冲突，与本地 node_modules 的安装方式保持一致
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --legacy-peer-deps

COPY frontend ./
RUN npm run build

# ---------- 运行阶段：Nginx 托管静态文件并反向代理后端 API ----------
FROM nginx:1.27.4-alpine

COPY docker/nginx/frontend.conf /etc/nginx/conf.d/default.conf
COPY docker/nginx/api_proxy.conf /etc/nginx/api_proxy.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

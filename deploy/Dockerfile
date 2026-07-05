# syntax=docker/dockerfile:1.7
# ===============================================
# Finance Exam System — 多阶段 Dockerfile
# ===============================================
# Stage 1: 前端构建  (node:20-alpine + npm, 与项目 package-lock.json 匹配)
# Stage 2: Python 后端运行时 (python:3.13-slim + uv)
#
# 设计要点:
# - 前端 packages/frontend/ 缺失时, Stage 1 仍能完成 (输出空 dist 占位)
#   这是通过 BuildKit --mount=type=bind + 条件检查实现的
# - Python 版本必须 ≥ 3.13 (与 packages/backend/pyproject.toml 一致)
# - 启动顺序: alembic upgrade head → uvicorn (idempotent)
# - /app/static 目录存放前端构建产物; 若前端未构建则放占位 index.html
#
# 构建命令 (从项目根):
#   docker build -f deploy/Dockerfile -t finance-exam-system:latest .
#
# 仅校验语法 (BuildKit check, 不实际打包):
#   docker build --check -f deploy/Dockerfile .

# ===============================================
# Stage 1: 前端构建
# ===============================================
FROM node:20-alpine AS frontend-builder

WORKDIR /build

# 始终先建空 dist 占位 — 保证 Stage 2 复制时不会因源缺失而失败
RUN mkdir -p /build/dist

# 使用 BuildKit bind mount 把 packages/frontend/ 挂入;
# 即使该目录不存在, 挂载点会是空目录 (BuildKit 对 bind mount 的容错行为)
# 这样 COPY 不会失败, 后续条件判断可优雅降级到 "前端未构建"
#
# Phase 5 fix (smoke test): 项目用 npm (package-lock.json 105K, 无 pnpm-lock.yaml),
# 旧 pnpm install --frozen-lockfile 因缺 pnpm-lock.yaml 必失败.
# 改用 npm ci (与 package-lock.json 严格匹配) + npm run build.
RUN --mount=type=bind,source=packages/frontend,target=/mnt/frontend,ro \
    if [ -f /mnt/frontend/package.json ]; then \
        echo "[frontend] 检测到 package.json, 开始 npm 构建(阿里云镜像)" && \
        cp -r /mnt/frontend/. /build/ && \
        npm config set registry https://registry.npmmirror.com && \
        npm ci && \
        npm run build; \
    else \
        echo "[frontend] packages/frontend/package.json 不存在, 输出空 dist 占位"; \
    fi

# ===============================================
# Stage 2: Python 后端运行时
# ===============================================
FROM python:3.13-slim AS runtime

# 元信息
LABEL maintainer="finance-exam-system" \
      description="财务管理考试系统 - Backend + Static Frontend" \
      version="0.1.0"

# 安装系统依赖:
#   curl: 容器健康检查 (Dockerfile HEALTHCHECK)
#   ca-certificates: HTTPS 请求 (后续 DeepSeek API 调用需要)
#   tini: PID 1 信号转发 (优雅退出)
# 注意: 不装 gcc / python3-dev — uv 装的 wheel 包已含预编译产物
# 使用阿里云 Debian 镜像加速 apt (国内网络环境)
RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        tini && \
    rm -rf /var/lib/apt/lists/*

# 装 uv (从官方镜像 copy, 比 pip install 快几倍且版本固定)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# 先复制依赖描述, 利用 Docker layer cache — pyproject/uv.lock 变动才重装
COPY packages/backend/pyproject.toml packages/backend/uv.lock /app/

# 同步依赖 (--no-dev: 不装 pytest 等开发依赖, 生产镜像瘦身)
# 使用 cache mount 加速二次构建 + 阿里云 PyPI 镜像 (国内网络)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --index-url https://mirrors.aliyun.com/pypi/simple/

# 复制后端源码 (源码变动不影响依赖缓存层)
COPY packages/backend/app /app/app
COPY packages/backend/alembic /app/alembic
COPY packages/backend/alembic.ini /app/alembic.ini

# 复制前端 dist (从 Stage 1)
COPY --from=frontend-builder /build/dist /app/static

# === Build-time baked data ===
# finance.db contains 628 questions (fin-mgmt 565 + corp-strat 63) across 2 subjects.
# 双层策略: 此 build-time COPY 保证全新拉起即可用;
# runtime 云服务器 mount 自定义 db 可覆盖（见 docker-compose.yml volume）。
COPY data/final/finance.db /app/data/final/finance.db
# pool.json: 评语池 — 21 条鼓励语, repair_service.py 多路径候选解析
# (dev: 项目根/data/praise/pool.json, docker: /app/data/praise/pool.json, builtin fallback)
COPY data/praise/pool.json /app/data/praise/pool.json

# 容器内 /app/data 用于 SQLite 持久化
# docker-compose.yml 通过 volume 挂载 -v ./data:/app/data
# 注意: 容器内 UID/GID 与宿主不一定一致, 这里不强制 chown
RUN mkdir -p /app/data && \
    printf '%s\n' \
        '#!/bin/sh' \
        '# Entrypoint placeholder — 实际启动由 CMD 控制' \
        'exec "$@"' \
        > /entrypoint.sh && \
    chmod +x /entrypoint.sh

# 使用 tini 作为 PID 1 — 正确处理 zombie 进程 + 信号转发 (uvicorn 优雅退出)
ENTRYPOINT ["/usr/bin/tini", "--", "/entrypoint.sh"]

# 健康检查 — 复用 FastAPI /health 端点
# 注: docker-compose.yml 也定义 healthcheck, 二选一即可 (此处保留为单容器独立运行时的兜底)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# 暴露端口
EXPOSE 8000

# 启动命令: 先 alembic 迁移 (idempotent, 已迁移则 no-op), 再 uvicorn
# alembic.ini 路径已 COPY 到 /app/alembic.ini, 工作目录是 /app
# Phase 5 fix: uv sync 把 alembic/uvicorn 装到 .venv/bin/, 不在 PATH — 必须用绝对路径
CMD ["sh", "-c", "/app/.venv/bin/alembic upgrade head && exec /app/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000"]
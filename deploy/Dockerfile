# syntax=docker/dockerfile:1.7
# ===============================================
# Finance Exam System — Python 后端 + 预构建前端
# ===============================================
# 前端 dist 已本地预构建并 commit 到仓库 (packages/frontend/dist/)
# Docker 镜像直接从 host COPY, 无需在容器内跑 npm install/build
# ===============================================
# Stage: Python 后端运行时 (python:3.13-slim + uv)
#
# 设计要点:
# - 前端 packages/frontend/dist/ 已预构建入 git, 容器内直 COPY
#   远程服务器不需要 Node/npm, 避免弱网下 npm install 超时
# - Python 版本必须 ≥ 3.13 (与 packages/backend/pyproject.toml 一致)
# - 启动顺序: alembic upgrade head → uvicorn (idempotent)
# - /app/static 目录存放前端构建产物
#
# 构建命令 (从项目根):
#   docker build -f deploy/Dockerfile -t finance-exam-system:latest .
#
# 本地预构建前端 (提交前):
#   cd packages/frontend && pnpm build

# ===============================================
# Python 后端运行时
# ===============================================
FROM python:3.13-slim AS runtime

# 元信息
LABEL maintainer="finance-exam-system" \
      description="财务管理考试系统 - Backend + Static Frontend" \
      version="0.1.0"

# 安装系统依赖:
#   curl: 容器健康检查 (Dockerfile HEALTHCHECK)
#   ca-certificates: HTTPS 请求 (后续 DeepSeek API 调用需要)
# 注意: 不装 gcc / python3-dev — uv 装的 wheel 包已含预编译产物
RUN apt-get update && \
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
# 使用 cache mount 加速二次构建
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# 复制后端源码 (源码变动不影响依赖缓存层)
COPY packages/backend/app /app/app
COPY packages/backend/alembic /app/alembic
COPY packages/backend/alembic.ini /app/alembic.ini

# 复制前端 dist (本地预构建, 已 commit 到仓库)
COPY packages/frontend/dist /app/static

# === Build-time baked data ===
# finance.db contains 628 questions (fin-mgmt 565 + corp-strat 63) across 2 subjects.
# 双层策略: 此 build-time COPY 保证全新拉起即可用;
# runtime 云服务器 mount 自定义 db 可覆盖（见 docker-compose.yml volume）。
COPY data/final/finance.db /app/data/final/finance.db

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
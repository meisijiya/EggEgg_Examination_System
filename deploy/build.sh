#!/usr/bin/env bash
# ===============================================
# Finance Exam System — 构建辅助脚本
# ===============================================
#
# 用途: 在 docker build 之前确保 packages/frontend/dist/ 存在
#       即使前端项目尚未初始化, 也能让后端镜像构建成功
#
# 用法:
#   ./deploy/build.sh                          # 默认 tag: finance-exam-system:latest
#   ./deploy/build.sh finance-exam-system:v0.2 # 自定义 tag
#
# 设计决策:
#   - 不创建 packages/frontend/ 占位 — 那是项目结构决策, 不是构建脚本职责
#   - 仅确保 dist/ 目录存在 (Dockerfile 用 bind mount + 条件检查已容错, 这是双保险)
#   - 把 build context 切到项目根 (Dockerfile 路径相对)

set -euo pipefail

# 项目根 (本脚本所在 deploy/ 的父目录)
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

# 自定义 tag
IMAGE_TAG="${1:-finance-exam-system:latest}"

# 决策: 即便 frontend 不存在, bind mount 会挂空目录; dist 占位进一步保证兼容性
if [ ! -d "packages/frontend/dist" ]; then
    echo "[build] packages/frontend/dist/ 不存在, 创建空目录占位"
    mkdir -p packages/frontend/dist
fi

echo "[build] 使用 Dockerfile: deploy/Dockerfile"
echo "[build] 构建上下文:    $PROJECT_ROOT"
echo "[build] 目标镜像:      $IMAGE_TAG"
echo

docker build \
    -f deploy/Dockerfile \
    -t "$IMAGE_TAG" \
    "$PROJECT_ROOT"

echo
echo "[build] 完成. 启动: docker compose -f deploy/docker-compose.yml up -d"
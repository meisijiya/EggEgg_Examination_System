# 🥚 财务管理考试系统 (Finance Exam System)

> **一句话定位**: 个人向 / 自用的小型考试练习系统 — 财务管理题库 + 模拟考 + AI 讲解.
> MVP 阶段面向"女朋友刷题用", 后续可演进为通用 SaaS.

---

## 📦 项目结构

```
EggEgg_Examination_System/
├── packages/
│   ├── backend/          # FastAPI 后端 (Python 3.13 + uv)
│   ├── preprocessor/     # PDF 解析 + 题库构建脚本
│   └── frontend/         # Vue 3 + Vite SPA (Phase 3, 进行中)
├── data/
│   ├── final/finance.db  # 题库 SQLite (运行时只读, volume 挂载)
│   ├── parsed/           # 题目 JSONL (入 git)
│   ├── distributions/    # 章节×题型概率配置 (入 git)
│   └── qa/               # QA 报告 (入 git)
├── deploy/               # 部署配置 (Dockerfile / Compose / Nginx)
├── openspec/             # OpenSpec 变更管理 (proposal / tasks / design)
└── .env.example          # 生产环境变量模板 (合并 backend 的版本)
```

---

## 🚀 部署 (生产环境)

### 前提

- ☁️ **云服务器**: 1 vCPU / 1 GB 内存 (国内节点, 延迟可接受)
  - 推荐: 阿里云 ECS 突发性能 t6 / 腾讯云 S5.SMALL2
- 🔒 **TLS 终止**: 云服务商反代 (阿里云 SLB / 腾讯云 CLB / Cloudflare) — 已配 HTTPS + 域名
- 🐳 **Docker**: 24+ (含 Compose v2)
- 🔑 **域名**: A 记录指向服务器公网 IP

### 首次部署步骤

```bash
# 1. 克隆代码
git clone <repo-url> /opt/finance-exam
cd /opt/finance-exam

# 2. 配置环境变量 — 复制模板并填真实值
cp .env.example .env
nano .env  # 或 vim

# 2a. 生成 JWT_SECRET (替换 .env 中的占位符)
python3 -c "import secrets; print(secrets.token_urlsafe(32))"

# 3. 准备题库数据 (一次性的, 之后跳过此步)
#    注: 当前 MVP 仓库 data/final/finance.db 已就绪, 无需重跑
#    若需重新生成: python -m preprocessor

# 4. 构建并启动容器
docker compose -f deploy/docker-compose.yml --env-file .env up -d --build

# 5. 验证
curl -fsS http://127.0.0.1:8000/health
# 期望: {"status":"ok","database":true,"question_count":N,"app_name":"Finance Exam System"}

# 6. 云反代 → 容器: 配置后端 upstream = http://<server-ip>:8000
#    然后通过 https://your-domain.com 访问

# 7. 登录
#    浏览器访问 https://your-domain.com/login (前端就绪后)
#    或直接 POST /auth/login 拿 JWT
```

### 反代前置说明

部署模型:

```
浏览器 ─HTTPS─→ 云反代 (SLB/CLB/CF) ─HTTP─→ 容器 :8000
                  ↑ 终止 TLS                ↑ uvicorn
                  ↑ 域名解析
```

云反代需配置:
- **监听**: 443, HTTPS
- **后端**: `http://<server-ip>:8000`
- **透传 headers**: `X-Forwarded-For` / `X-Real-IP` / `X-Forwarded-Proto` — 让 FastAPI 拿到真实客户端 IP
- **健康检查**: GET `/health` 路径, 30s 间隔

若不用云反代而直接用 nginx: 见 [`deploy/nginx.example.conf`](deploy/nginx.example.conf).

### 数据备份

题库 (`data/final/finance.db`) 不需要备份 — 源代码 + `preprocessor/build_db.py` 可重生成.

应用库 (`data/app.db`) **必须**备份 — 包含用户考试记录:

```bash
# 手动备份 (建议配合 cron 每日跑)
docker compose -f deploy/docker-compose.yml exec -T finance-exam \
    sqlite3 /app/data/app.db ".backup '/app/data/app.db.$(date +%Y%m%d)'"
tar czf app.db.tgz data/app.db*
ossutil cp app.db.tgz oss://your-bucket/backups/  # 或 rclone / aws s3 cp
```

自动备份建议: crontab + ossutil, 每日凌晨 03:00 跑一次, 保留 30 天滚动.

### 升级流程

```bash
# 拉取最新代码
git pull origin main

# 重新构建镜像 (--build 会触发前端 rebuild + 后端依赖同步)
docker compose -f deploy/docker-compose.yml build

# 重启容器 (新镜像替换旧容器, SQLite 数据通过 volume 持久化)
docker compose -f deploy/docker-compose.yml up -d

# 查看日志确认启动正常
docker compose -f deploy/docker-compose.yml logs --tail=50 -f
```

### 部署架构图

```
┌─────────────────────────────────────────────────┐
│                   浏览器                         │
│           (HTTPS, https://domain)                │
└────────────────────┬────────────────────────────┘
                     │ TLS 终止
                     ▼
┌─────────────────────────────────────────────────┐
│         云反代 (SLB / CLB / Cloudflare)         │
│    - HTTPS cert                                  │
│    - X-Forwarded-For / X-Real-IP 透传           │
└────────────────────┬────────────────────────────┘
                     │ HTTP (内网 / 127.0.0.1)
                     ▼
┌─────────────────────────────────────────────────┐
│   Docker Host (云服务器 1 vCPU / 1 GB)         │
│  ┌───────────────────────────────────────────┐  │
│  │  finance-exam-system 容器                 │  │
│  │  ┌─────────────────────────────────────┐  │  │
│  │  │  uvicorn :8000  (FastAPI)           │  │  │
│  │  │    ├─ /auth/login                   │  │  │
│  │  │    ├─ /exams/*                      │  │  │
│  │  │    ├─ /dashboard                    │  │  │
│  │  │    ├─ /admin/review/*               │  │  │
│  │  │    └─ /health                       │  │  │
│  │  └─────────────────────────────────────┘  │  │
│  │  ┌─────────────────────────────────────┐  │  │
│  │  │  /app/static  (前端 SPA 静态文件)   │  │  │
│  │  └─────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────┘  │
│       │                                          │
│       │ volume: ./data → /app/data               │
│       ▼                                          │
│  ┌─────────────────────────────────────────┐    │
│  │  SQLite 数据库                          │    │
│  │    - data/final/finance.db  (题库, RO)  │    │
│  │    - data/app.db           (应用库, RW) │    │
│  └─────────────────────────────────────────┘    │
└─────────────────────────────────────────────────┘
```

---

## 🛠️ 本地开发

### 后端

```bash
cd packages/backend
uv sync                              # 安装依赖 (含 dev group)
cp .env.example .env                  # 本地配置
uv run pytest -v                      # 跑测试 (62 用例)
uv run uvicorn app.main:app --reload # 开发服务器 (http://localhost:8000)
```

OpenAPI 文档: <http://localhost:8000/docs>

### 题库预处理

```bash
cd packages/preprocessor
# 一次性: 从 PDF 资料生成 finance.db
python build_db.py
```

---

## 📋 Tasks 进度

详见 [`openspec/changes/finance-exam-system-mvp/tasks.md`](openspec/changes/finance-exam-system-mvp/tasks.md).

**当前状态**: Phase 1+2 完成 (后端 MVP + Web API), Phase 3 进行中 (前端 SPA), Phase 4 完成 (部署).

---

## 📜 License

Personal project — all rights reserved.
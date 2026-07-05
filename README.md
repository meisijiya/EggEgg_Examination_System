# EggEgg Exam System

> 🥚 自托管的财务管理 + 公司战略和风险管理 双科目考试模拟系统。
> 单 Docker 容器一键拉起，镜像内 build-time baked **628 题**(fin-mgmt 565 + corp-strat 63)题库。

---

## 🚀 快速开始(Docker 一键部署)

> **目标读者**: 任何在远程/本地服务器拉起此系统的人 — 包括交给 AI Agent 自动部署(见下方 🤖 AI 一键部署 workflow)。

### 前置条件

- Linux / macOS / WSL2 任意发行版
- Docker **24+** + Docker Compose **v2**(验证: `docker compose version`)
- 4 GB RAM 起步(生产实测 peak 86 MB,留足 headroom)

### 6 步拉起

```bash
# 1. 克隆仓库
git clone git@github.com:meisijiya/EggEgg_Examination_System.git
cd EggEgg_Examination_System

# 2. 复制环境变量模板
cp .env.example .env

# 3. 编辑 .env 填入必填 4 项(见下方 § 配置信息清单)
nano .env  # 或 vim / VS Code / sed

# 4. 一键启动(后台 + 自动 build 镜像)
docker compose -f deploy/docker-compose.yml up -d --build

# 5. 等待 30s 让 uvicorn boot
sleep 30

# 6. 健康检查
curl http://127.0.0.1:8000/health
```

**期望响应**(镜像内含 finance.db 628 题):

```json
{"status":"ok","database":true,"question_count":628,"app_name":"Finance Exam System"}
```

- `status:"ok"` + `database:true` = 题库可读 + 题目数 ≥ 100 (见 `packages/backend/app/main.py` `_validate_question_db`)
- `question_count:628` = fin-mgmt **565** + corp-strat **63**
- 若返回 `status:"degraded"` 或 connection refused → 看 [OPERATIONS.md §4 故障排查](deploy/OPERATIONS.md)

### 配置信息清单(env vars 必填 + 可选)

#### 必填 4 项

| 变量 | 说明 | 示例 / 生成方法 |
|------|------|----------------|
| `JWT_SECRET` | JWT HS256 签名密钥,256-bit 熵 | `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `USER_PASSWORD` | 学员登录密码(`/auth/login`) | 自定义 ≥ 12 字符,含大小写 + 数字 |
| `ADMIN_PASSWORD` | 管理员密码(`/admin/review/*`,AI 出题 gate) | 自定义 ≥ 16 字符,含大小写 + 数字 + 符号 |
| `CORS_ORIGINS` | 允许的跨域来源(逗号分隔,**不支持 `*` 通配**) | `http://localhost:8000,https://your-domain.com` |

#### 可选 1 项

| 变量 | 说明 | 缺失时行为 |
|------|------|-----------|
| `DEEPSEEK_API_KEY` | AI 讲解 + AI 出题(multi-agent pipeline) | AI 讲解走 stub fallback,multi-agent pipeline **0 题 approved**(corp-strat 题库空,但不影响考试本身) |

> 📝 其他变量(`DATABASE_URL` / `APP_DB_URL` / `MIN_COVERAGE` / `JWT_EXPIRE_MINUTES` / `TZ` 等)有合理默认值,通常无需修改。详见 [.env.example](.env.example)。

---

## 🤖 AI 一键部署 workflow

> **如果用户(或另一个 AI Agent)把这段 README + 仓库 URL 交给 Claude / GPT / Codex 等 AI Agent,AI 应按以下流程自动完成部署:**

**步骤 1 — 收到任务**: 用户给类似这样的指令 —

> "在远程服务器部署 EggEgg Exam System,仓库地址是 `git@github.com:meisijiya/EggEgg_Examination_System.git`,这是 README(粘贴此文件)。请一键部署。"

**步骤 2 — AI 询问用户**: 4 个必填配置项

1. `JWT_SECRET`(可由 AI 现场用 `secrets.token_urlsafe(32)` 生成,无需问)
2. `USER_PASSWORD`(必须由用户提供)
3. `ADMIN_PASSWORD`(必须由用户提供)
4. `CORS_ORIGINS`(必须由用户提供 — 远程域名)

**步骤 3 — AI 在服务器上自动执行 6 步**:

```bash
git clone git@github.com:meisijiya/EggEgg_Examination_System.git
cd EggEgg_Examination_System

cp .env.example .env

# 写入用户提供的 4 个值(用 sed / echo / python 都行)
sed -i "s|^JWT_SECRET=.*|JWT_SECRET=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')|" .env
sed -i "s|^USER_PASSWORD=.*|USER_PASSWORD=<用户提供>|" .env
sed -i "s|^ADMIN_PASSWORD=.*|ADMIN_PASSWORD=<用户提供>|" .env
sed -i "s|^CORS_ORIGINS=.*|CORS_ORIGINS=<用户提供>|" .env

docker compose -f deploy/docker-compose.yml up -d --build
sleep 30
curl http://127.0.0.1:8000/health
```

**步骤 4 — AI 报告结果**:

- ✅ container `Up + healthy`(`docker ps`)
- ✅ `/health` 返回 `question_count:628`
- ⚠️ 询问用户是否需要进一步配置 — **反代 / TLS / CDN / 域名**(这些是 out-of-MVP-scope,见 [OPERATIONS.md §3 升级流程](deploy/OPERATIONS.md))

**步骤 5 — 完成**: 用户浏览器访问 `http://<server-ip>:8000` 或反代后的 `https://your-domain.com`。

---

## 📦 镜像内含(默认拉起即可用)

| 组件 | 说明 | 体积 |
|------|------|------|
| **finance.db** (build-time baked) | 单 SQLite 库,2 科目 10+ 章节 5 题型,628 题 | 296 KB |
| **Backend** (uvicorn + FastAPI) | Python 3.13 + SQLAlchemy 2 + Pydantic 2 + JWT | ~150 MB |
| **Frontend dist** (build-time copy) | Vue 3 SPA 静态文件(`/app/static`) | 视前端构建 |
| **alembic** (idempotent migration) | `app.db` schema 自动演进 | < 1 MB |

- **双层数据策略**: build-time `COPY data/final/finance.db` 入 image + runtime volume mount `./data:/app/data:rw` 允许云服务器覆盖(详见 [OPERATIONS.md §2](deploy/OPERATIONS.md))
- **资源限制**: CPU `0.8/0.2` / MEM `768M/256M` (实测 peak 86M,**充足**)
- **健康检查**: `/health` 30s 间隔,3 retries 失败 → 自动 `restart: unless-stopped`
- **日志**: json-file max-size `10m` × max-file `3`(防磁盘被填满)
- **时区**: `TZ=Asia/Shanghai`(中国用户时区一致)

---

## 🔧 进阶文档

- 📘 [**运维手册**](deploy/OPERATIONS.md) — 部署 / 升级 / 回滚 / 备份 / 故障排查 / 监控 / 安全审计
- 📗 [**科目 onboarding**](docs/SUBJECT_ONBOARDING.md) — 添加新科目的开发者模板(继 `fin-mgmt` / `corp-strat` 之后)
- 📕 [**项目开发规范**](AGENTS.md) — 接手项目的开发者 / agent 约束(中文 + emoji + 函数级注释 + 等)
- 📙 [**设计 Spec**](docs/superpowers/specs/2026-07-04-finance-exam-system-design.md) — 完整系统设计(1290 行,v8)

---

## 🛠️ 本地开发(非 Docker)

```bash
# 后端
cd packages/backend
uv sync                              # 安装依赖 (含 dev group)
uv run pytest -v                     # 跑测试 (288 tests pass)
uv run uvicorn app.main:app --reload # 开发服务器 (http://localhost:8000)

# 前端
cd packages/frontend
pnpm install
pnpm test                            # 102 tests pass
pnpm dev                             # Vite dev server (http://localhost:5173)

# 题库预处理(一次性 / 改题后)
cd packages/preprocessor
python build_db.py --subject fin-mgmt   # 重生成 finance.db
```

OpenAPI 文档: <http://localhost:8000/docs>

---

## 📊 当前状态(Phase 5 docker 部署验证)

| 维度 | 数据 |
|------|------|
| Backend tests | **288 / 288 ✅** |
| Frontend tests | **102 / 102 ✅** |
| `fin-mgmt` 题库 | **565 题** UNCHANGED |
| `corp-strat` 题库 | **63 题**(5 题型全覆盖) |
| `finance.db` size | **296 KB** / 628 题 |
| Container | `Up + healthy + :8000 listening` ✅ |
| docker memory peak | **86 MB / 768M 限** (充足) |
| `/health` response | `{"status":"ok","database":true,"question_count":628,...}` |

> 📋 Tasks 进度: 详见 [`openspec/changes/archive/2026-07-05-finance-exam-system-mvp/tasks.md`](openspec/changes/archive/2026-07-05-finance-exam-system-mvp/tasks.md)(Phase 0-4 全部完成,Phase 5 docker 验证完成)。

---

## 📜 License

Personal project — all rights reserved.

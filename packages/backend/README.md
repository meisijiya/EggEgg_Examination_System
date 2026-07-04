# Finance Exam System — Backend (Phase 1 + 2 + 3 + 5 整合 MVP)

FastAPI + SQLAlchemy 2 (async) + SQLite + Alembic 后端实现。

> Phase 1（后端 MVP）+ Phase 2（Web API 端点）+ Phase 3（前端 SPA）+ Phase 5（整合 fixes）已完成。
> 出题 / 判分 零 LLM 调用；AI 讲解模块按需触发，未配置 DeepSeek 时 graceful fallback。
> 静态文件由后端单端口托管前端 dist（开发期 + 容器期均可工作）。

---

## 📦 项目结构

```
packages/backend/
├── app/
│   ├── api/                  # FastAPI 路由
│   │   ├── auth.py           # POST /auth/login + 鉴权依赖
│   │   ├── exams.py          # /exams/* （start/get/submit/result）
│   │   ├── explain.py        # POST /exams/{id}/explain (stub)
│   │   ├── dashboard.py      # GET /dashboard
│   │   └── admin.py          # GET /admin/review/* （admin 鉴权）
│   ├── models/               # SQLAlchemy ORM
│   │   ├── database.py       # 双引擎（题库 + 应用库）
│   │   ├── question.py       # Subject / Chapter / Question
│   │   └── attempt.py        # ExamAttempt / AttemptAnswer
│   ├── services/             # 业务逻辑
│   │   ├── auth_service.py   # 单密码 JWT
│   │   ├── paper_assembler.py# 出题（章节×题型×难度三维加权）
│   │   ├── grader.py         # 客观对照 + 关键词覆盖率
│   │   └── deepseek_client.py # DeepSeek 客户端（OpenAI 兼容协议 + SSE 流式）
│   ├── schemas.py            # Pydantic 2 strict (extra='forbid')
│   ├── config.py             # pydantic-settings
│   └── main.py               # FastAPI 入口（lifespan + 路由 + /health）
├── tests/
│   ├── test_grader.py        # 26 个判分测试
│   ├── test_paper_assembler.py # 5 个抽题测试（100 次模拟）
│   ├── test_auth.py          # 17 个认证测试
│   ├── test_api.py           # 14 个 API 集成测试
│   └── test_static.py        # 10 个静态 + SPA fallback 测试
├── alembic/                  # 迁移
├── alembic.ini
├── pyproject.toml            # uv 管理
├── .env.example              # 环境变量模板
├── pytest.ini
└── README.md
```

---

## 🚀 本地启动

### 前置依赖

- Python 3.13+
- [`uv`](https://github.com/astral-sh/uv)（推荐）
- 题库 `data/final/finance.db` 已就绪（由 preprocessor 阶段产出）

### 1. 安装依赖

```bash
cd packages/backend
uv sync
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，至少修改：
#   JWT_SECRET=<随机 32 字节>
#   USER_PASSWORD=<学员密码>
#   ADMIN_PASSWORD=<管理员密码>
# 可选（不配则 explain 走 stub fallback）：
#   DEEPSEEK_API_KEY=<DeepSeek / OpenAI 兼容 key>
#   DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
#   DEEPSEEK_MODEL=deepseek-chat
```

### 3. 数据库迁移（应用库 exam_attempts / attempt_answers）

```bash
uv run alembic upgrade head
```

### 4. 启动服务

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

启动后访问：
- `http://localhost:8000/health` — 健康检查
- `http://localhost:8000/docs` — Swagger UI（OpenAPI 自动生成）
- `http://localhost:8000/openapi.json` — OpenAPI Schema
- `http://localhost:8000/` — 若前端已 build → SPA 首页；否则 → JSON 提示

---

## 🧪 测试

```bash
# 全部测试
uv run pytest

# 覆盖率
uv run pytest --cov=app --cov-report=term-missing

# 单独测试某模块
uv run pytest tests/test_grader.py -v
```

**当前覆盖率**：82%（超过目标 80%）。

---

## 🌐 前端集成（Phase 5 整合要点）

后端**单端口**托管前后端，无需 nginx 反代静态资源。

### 静态文件路径

启动时 `_resolve_static_dir()` 会按以下顺序探测：

| 路径 | 适用场景 |
|---|---|
| `{repo}/packages/frontend/dist` | 开发期：uvicorn CWD = `packages/backend` |
| `/app/static` | 容器期：多阶段构建把 dist COPY 到 `/app/static` |

两者都不存在 → 静态服务关闭（API 仍可访问）。

### 三种启动模式

| 模式 | 后端行为 | 用户访问 |
|---|---|---|
| 后端单独启动（dist 不在） | `GET /` 返回 JSON 提示 `Frontend not built` | 仅 API + `/docs` |
| 后端 + 前端 dist 都在 | `GET /` 服务 `index.html`；`/assets/*` 静态托管 | 完整 SPA，浏览器直达 `http://localhost:8000/` |
| 反代部署（`docker compose`） | 容器内 dist 已 COPY 到 `/app/static` | 同上，无需单独 nginx 静态路径 |

### SPA fallback middleware

任何**非 API 前缀**（`/api` `/auth` `/exams` `/health` `/admin/review` `/assets`）且未命中真实路由的 `GET` 请求，404 状态会被 middleware 接管，返回 `index.html`。

```python
# 关键代码（main.py）
class SPAFallbackMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.method != "GET":
            return await call_next(request)
        response = await call_next(request)
        if response.status_code != 404:
            return response
        # 不命中 API 前缀 + 静态文件扩展名 → 返回 index.html
        if any(path.startswith(p) for p in _API_PREFIXES):
            return response
        if "." in path.rsplit("/", 1)[-1]:  # .css/.js/.png...
            return response
        return FileResponse(STATIC_DIR / "index.html")
```

支持场景：
- `GET /admin` → 200 HTML（前端 SPA 路由的 admin 页）
- `GET /random-page-xyz` → 200 HTML（浏览器刷新）
- `GET /admin/review/queue` 无 token → 401 JSON（API 真路径，未触发 fallback）
- `GET /assets/missing.css` → 404（静态扩展名不被 fallback 吃掉）

### 反代部署

容器期（Caddy / nginx）反代只需指向 `:8000`，**不需要**额外的 `location /assets` 静态规则 —— 后端已经托管。

```nginx
# 反代示例（Caddy 等价）
location / {
    proxy_pass http://127.0.0.1:8000;
}
```

---

## 🔌 API 端点

### 鉴权

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| `POST` | `/auth/login` | 无 | 单密码登录，返回 JWT |

### 考试流程（user JWT）

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/exams/start` | 启动模拟考，返回 attempt_id + 41 道题 |
| `GET` | `/exams/{id}` | 拉取试卷（断线重连用） |
| `POST` | `/exams/{id}/submit` | 交卷 + 同步判分（≤ 2s） |
| `GET` | `/exams/{id}/result` | 完整成绩详情（含每题评语） |
| `POST` | `/exams/{id}/explain` | DeepSeek 已配 → 流式 SSE；未配 → stub JSON（graceful fallback） |

### 仪表盘（user JWT）

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/dashboard` | 历次成绩 + 趋势 + 章节雷达 |

### 题目 Review（admin JWT）

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/admin/review/queue` | 可疑题列表（缺解析 / 缺 key_points） |
| `POST` | `/admin/review/questions/{id}` | 人工修正题目 |

### 系统

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| `GET` | `/health` | 无 | 健康检查（返回题库题数） |
| `GET` | `/docs` | 无 | Swagger UI |

---

## 🧠 核心设计要点

### 1. 双数据库

- **题库 `data/final/finance.db`**（只读）：由 preprocessor 写入，含 565 题（9 章 × 4 题型）
- **应用库 `data/app.db`**（读写）：存 exam_attempts + attempt_answers（Alembic 管理）

两库物理隔离，跨库 JOIN 不可能；运行时用两次查询 + Python 端关联。

### 2. 出题算法（paper_assembler）

按 spec §6.2 / §6.3：
- 章节权重 ∝ 该章节该题型可用题数（保护性约束 ≥ 3）
- 难度目标分布：easy 30% / medium 50% / hard 20%
- 至少覆盖 9 章中的 8 章（不足时最后一轮替换补救）
- comprehensive 题型在数据集中缺失 → fallback 到 calc（运行时透明）

### 3. 判分算法（grader）

按 spec §6.4：
- **客观题**（single/multi/judge）：选项集合相等才算对（漏选/多选/错选全错）
- **主观题**（calc/comprehensive）：key_points 子串匹配，覆盖率 ≥ 0.6（可调）按比例给分
- 答案 < 5 字 → 0 分 + 评语"答案过短"
- key_points 为空（数据缺失）→ 退化为参考答案完全一致才得分

### 4. 鉴权

单密码 JWT（HS256）：
- `USER_PASSWORD` → role=user → 可访问考试 + dashboard
- `ADMIN_PASSWORD` → role=admin → 额外可访问 `/admin/*`

### 5. 性能目标

- 出题（assemble 41 题）：< 500ms
- 判分（41 题）：< 1s
- 完整 start → submit 链路：< 2s

---

## ⚠️ 已知数据/规格偏差

1. **comprehensive 题型**：数据集中没有 comprehensive 类型题目（preprocessor 只产出了 4 种：single/multi/judge/calc）。运行时 comprehensive slot 自动 fallback 到 calc，题目实际 type 仍是 calc（不影响判分）。

2. **总分 100 vs 110**：spec 标注"= 100 分"，但实际数值（15×2 + 10×3 + 10×1 + 4×5 + 2×10）= 110 分。当前实现按数值生成（110 分），保留 spec 描述。

3. **JWT_SECRET 长度警告**：默认 dev 配置 29 字节 < 推荐的 32 字节。生产前务必替换为更长随机串。

---

## 📚 后续（不在本期范围）

- ✅ **Phase 1** — 后端 MVP（已完成）
- ✅ **Phase 2** — Web API（已完成）
- ✅ **Phase 3** — 前端 Vue 3 SPA（已完成）
- ✅ **Phase 5** — 整合 fixes：StaticFiles + SPA fallback + DeepSeek stub fallback（已完成）
- ⏳ **Phase 6** — Docker 化部署（反代 -> :8000）

---

## 引用文档

- 设计 Spec：`docs/superpowers/specs/2026-07-04-finance-exam-system-design.md`
- OpenSpec tasks：`openspec/changes/finance-exam-system-mvp/tasks.md`
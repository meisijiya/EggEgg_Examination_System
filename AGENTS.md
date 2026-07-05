# 项目级 AGENTS.md 规范

> EggEgg_Examination_System 项目级开发规范。 全局规则见 [`~/.config/opencode/AGENTS.md`](file:///home/ljh2923/.config/opencode/AGENTS.md)。
> 架构详细见 [`spec`](docs/superpowers/specs/2026-07-04-finance-exam-system-design.md) + [`README.md`](README.md)。

---

## 1. 🛠️ 技术栈

| 层 | 选型 |
|---|---|
| 后端 | Python 3.13 + FastAPI + SQLAlchemy 2 + SQLite(WAL) + Pydantic 2 + JWT(HS256) |
| 前端 | Vue 3 + Vite + TS + Element Plus + ECharts + Pinia + Vue Router |
| 测试 | pytest(后端) + Vitest(前端) |
| AI 讲解 | DeepSeek(`openai` Python SDK, 仅 `/exams/{id}/explain` + mixed mode) |
| AI 出题 | DeepSeek + DuckDuckGo HTML(无 API key) |
| 包管理 | uv(后端) + pnpm(前端) |
| 部署 | Docker Compose 单容器 + 云反代前置 |

---

## 2. 📁 项目结构

```
packages/{backend,preprocessor,frontend}    data/{parsed,distributions,qa,final}    deploy/    docs/superpowers    openspec/    {公司战略和风险管理,财务管理资料}/    frontend-UI/
```

详见 [`README.md`](README.md) §项目结构 + [`spec §15`](docs/superpowers/specs/2026-07-04-finance-exam-system-design.md)。

---

## 3. 📐 开发约定

- **中文回复 + Emoji** — 所有 user-facing 输出中文 + 适当 emoji(用户硬约束)
- **函数级注释** — 中文 docstring / JSDoc, 描述目的 + 参数 + 返回值(全局 §"函数级注释")
- **PATH 验证** — `which <tool>` 验证非常规工具归属(全局 §"PATH 验证")
- **Python 库文档** — 优先 `npx ctx7@latest library <name>`(全局 §"context7")
- **零 schema migration** — DB schema 改动 = `INSERT subjects/chapters` + Pydantic 字面量扩展 `QuestionType`(`schemas.py`), 不再生成 Alembic 版本
- **Ponytail lite** — 写完自检: 过度抽象 / 死代码 / 不必要间接层
- **Designer Handoff Guardrail** — 改前端样式**必须 route 回 `@designer`**, `@fixer` 仅机械修改

---

## 4. 🧪 跑测试 / 构建

```bash
# 后端
cd packages/backend && uv sync && uv run pytest -v
# 前端
cd packages/frontend && pnpm install && pnpm test && pnpm build
# Preprocessor(重生成 finance.db)
cd packages/preprocessor && python build_db.py --subject fin-mgmt
```

> 全局规则: Java 项目在 Docker Desktop; **Python/Node 不在此限**(本项目是 Python)。

---

## 5. 🐳 Docker 部署 + 🔐 .env

```bash
docker compose -f deploy/docker-compose.yml --env-file .env up -d --build
cp .env.example .env && python -c "import secrets; print(secrets.token_urlsafe(32))"
```

运维细节见 [`deploy/OPERATIONS.md`](deploy/OPERATIONS.md)。`.env` **不入 git**; 必填 `JWT_SECRET` / `USER_PASSWORD` / `ADMIN_PASSWORD` / `CORS_ORIGINS`; `DEEPSEEK_API_KEY` 缺失 → AI 讲解 fallback, 不影响考试。

---

## 6. 🚫 排除敏感(跨域)

| 类型 | 处理 |
|---|---|
| API key / token / 私钥 | ❌ 不入 git / IMA, 仅 `.env` |
| 内部 IP / 内部域名 | ❌ 不入 git / IMA |
| 客户名单 / 合同金额 | ❌ 不入 git / IMA |
| 真实姓名 + 项目关联 | ⚠️ 脱敏(代号) |
| 题库 metadata(题目+答案) | ✅ 入 git(`data/parsed/`) |
| 用户考试记录(app.db) | ❌ 不入 git, runtime 备份到 OSS |

> 📚 全局 [`AGENTS.md`](file:///home/ljh2923/.config/opencode/AGENTS.md) / [`spec`](docs/superpowers/specs/2026-07-04-finance-exam-system-design.md) / [`README.md`](README.md)
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

---

## 7. 🔄 预处理数据管线（Data Pipeline Contract）

> **核心原则**：所有科目共享同一个 `data/final/finance.db` SQLite schema。后端 API（`/subjects`、`/exams/start`、`/exams/{id}/submit` 等）**只读不写**此库，新增科目只需 INSERT 行，**永远不需要改后端代码**。

### 7.1 管线架构（3 阶段）

```
原始资料（PDF / DOCX）
  │
  ├── ① 有答案 PDF  → parse_questions.py → questions.jsonl（Pydantic 校验）
  │   └── difficulty 标注 → difficulty/ch{1..N}.jsonl
  │
  ├── ② 无答案 DOCX → parse_docx.py → segments JSONL
  │   └── multi-agent AI 出题 → ai_generated.jsonl
  │       └── admin review gate → status='approved'
  │
  └── ③ build_db.py --subject <id> [--ai-approved-jsonl <path>]
        │
        └── data/final/finance.db  ← 后端 API 只读消费
```

### 7.2 单库契约（Single-DB Contract）

| 规则 | 说明 |
|---|---|
| **一库多科** | 所有科目入同一个 `data/final/finance.db`，按 `subject_id` 区分 |
| **INSERT 不 ALTER** | 新科目 = `INSERT subjects` + `INSERT chapters`，**不**加列 / 不改 schema / 不跑 Alembic |
| **后端零修改** | 只要 `subjects` / `chapters` / `questions` 三表数据对，现有 API 全部直接可用 |
| **`question_count` 自动** | `/subjects` 端点用 `SELECT COUNT(*) ... GROUP BY subject_id`，不用手动维护计数 |

### 7.3 Schema 契约（后端读哪些列）

新增科目预处理产出**必须**对齐以下字段，否则后端 API 运行时报错：

| 表 | 关键列 | 约束 | 后端 API 使用方 |
|---|---|---|---|
| `subjects` | `id` TEXT PK, `name` TEXT | `id` 用 kebab-case（如 `fin-mgmt`） | `/subjects`、`StartExamRequest` 校验 |
| `chapters` | `subject_id`, `code` TEXT（如 `ch1`）, `title`, `weight` REAL | `UNIQUE(subject_id, code)` | `paper_assembler` 章节加权抽样 |
| `questions` | `type` TEXT, `difficulty` **INTEGER 1/2/3**, `stem`, `answer`, `options_json`, `key_points_json` | `CHECK(difficulty IN (1,2,3))` | `/exams/start` 出题、`grader` 判分 |

### 7.4 Pydantic 规范（权威 schema）

- **规范源**：`packages/preprocessor/parse_questions.py` 中的 `Question(BaseModel)` 类 — `extra='forbid'`
- 所有解析脚本**必须**产出符合此模型的记录，`build_db.py` 会做全量 `ValidationError` 校验
- 新题型 → 先在 `packages/backend/app/schemas.py` 的 `QuestionType` Literal 中加字面量，再扩展解析器
- `options_json` / `key_points_json`：只对特定题型序列化 JSON 数组（`single/multi/judge` → options；`calc/comprehensive/short_answer/case_analysis` → key_points）

### 7.5 AI 出题 gate（仅 DOCX 路径）

```
ai_generated.jsonl (status='pending')
  → admin review (GET /admin/ai-generated-questions, POST approve/reject)
  → status='approved' 的行
  → build_db.py --ai-approved-jsonl <path> 合并入库
```

- `build_db.py` **只加载 `status='approved'` 的行**，其他 status 静默跳过
- 用户不在 admin 页面时可用 `auto_approve_ai.py` 按 `confidence ≥ 0.6 + peer_review agree` 自动 approve

### 7.6 新增科目 checklist（操作顺序）

1. **[ ] 资料放置**：原始 PDF/DOCX 放到项目根目录下独立文件夹（如 `公司战略和风险管理/`）
2. **[ ] 解析适配**：`parse_questions.py --pdf-dir` 或 `parse_docx.py --docx-dir` （调整 chapter 白名单）
3. **[ ] chapter 映射**：在 `build_db.py` 调用时传 `--chapter-titles-json` 或硬编码 `CHAPTER_TITLES` 字典
4. **[ ] difficulty 标注**：确保每个 `questions.jsonl` 行有对应的 `difficulty/chN.jsonl` 记录
5. **[ ] 入库**：`python build_db.py --subject <id> --subject-name <名称> --output-db data/final/finance.db`
6. **[ ] 验证**：`sqlite3 data/final/finance.db "SELECT COUNT(*) FROM questions WHERE subject_id='<id>'"` ≥ 预期题数
7. **[ ] 前端**：无需改动 — `/subjects` 端点自动发现新科目，`SubjectSwitcher` 自动渲染 dropdown
8. **[ ] `.gitignore`**：原始资料文件夹（PDF/DOCX）不入 git；`data/parsed/*.jsonl` 入 git

> 📚 详细见 [`docs/SUBJECT_ONBOARDING.md`](docs/SUBJECT_ONBOARDING.md) / [`spec §5`](docs/superpowers/specs/2026-07-04-finance-exam-system-design.md) / [`README.md`](README.md)

> 📚 全局 [`AGENTS.md`](file:///home/ljh2923/.config/opencode/AGENTS.md) / [`spec`](docs/superpowers/specs/2026-07-04-finance-exam-system-design.md) / [`README.md`](README.md)
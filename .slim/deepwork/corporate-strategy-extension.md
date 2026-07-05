# EggEgg Exam System — 公司战略和风险管理 扩展（deepwork）

> 新 deepwork session（接续 finance Phase 0-3 已 archive-ready）

## Goal

3 段长程任务，全部要求委派 oracle 审查（深 work）：

1. **优化已有**：题目显示「第几章」（做题时 + 查阅成绩时都看到）；混合模式 AI 改编题需要明显标识
2. **继续开发「公司战略和风险管理」科目**：
   - 前端顶部新增「科目」选项，提供切换
   - **最大限度复用**当前已有接口 + 前端设计规范
   - 预处理按 2 种情况切分：
     - ① **题目+答案成对** → 类似财务管理科目预处理的逻辑
     - ② **只有题目 / 只有知识点** → Agent 团队提前生成题目+答案；引用资料的哪个内容（折叠栏，默认收起）；答案标明 AI 生成
   - 3 张图片是**简答题 + 无答案**，归第②种情况
     - `5942a7cb0beeb955f33fbd0b74677bf4.png`
     - `e63d7f3d905ea7bac0df2a02a63a683a.png`
     - `9662a07326680e786244ac03311e66d9.png`
   - **考试题型**：单选、多选、判断、**简答**、**案例分析**
3. **部署 + 入库**：内存不溢出 + DB 稳定 + docker 一键拉起（前后端 + 数据库 + 预处理数据）+ 提供 env.example + **排除敏感文件** → 提交到远程仓库

## Constraints & Preferences

- 复用 finance 已建架构（FastAPI + SQLAlchemy 2 + SQLite + JWT + admin）
- 复用 des-1 已落地的雪天/蓝天/旭日 设计令牌（`packages/frontend/src/styles/{tokens,element-overrides,global}.css`）
- 复用 finance 已有的 PDF 解析 + AI 难度评估 + Oracle 审查 预处理 pipeline
- 复用 `data/parsed/`、`data/qa/oracle_review.md`、`data/qa/import_report.md` 体系
- 用户硬约束：**递归** oracle 审查（每 phase 都要委派）；simplify 反馈必收
- 排除敏感文件清单（待 Phase 3 锁定）：`.env`、`coverage.json`、`*.cover`、`packages/backend/.coverage`、`data/final/*.db`、`*.pdf` 原始资料、`DEEPSEEK_API_KEY`
- 已有 .gitignore 已忽略 `.slim/deepwork/` / `packages/backend/data/*.db` / `*.cover` / `coverage.json`
- 中文回复 + emoji；函数级注释
- 已熟 finance 系统：spec v8 1290 行 / 130+41 测试 / mixed 28s / `data/final/finance.db` 256KB（9 chapters / 565 questions）

## Progress

### Done
- finance Phase 0-3 已 archive-ready（待 `/comet-archive` 触发）
- spec v8 + decision log #1-#21 已稳定

### In Progress
- **Phase 0** Recon + Plan Review
  - exp-1 派发（recon codebase 3 任务）：chapter 来源 / AI 改编标识 / subject 切换 / Docker 深度 / preprocess 参数化 / 5 题型支持 / `@公司战略和风险管理/` 目录结构
  - ora-1 reuse 派发（review 5-phase plan + simplify + integration risks）

### Blocked
- (none)

## Key Decisions

- 进度文件按 deepwork skill 规约置 `.slim/deepwork/corporate-strategy-extension.md`（新文件，finance 那个不动）
- **每 phase 必委派 oracle 审查**（用户硬约束）
- **每 phase oracle 必收 simplify/readability 反馈**（deepwork skill 硬约束）
- **schema 扩展策略**：examination 级新增 `subject_id` / question 级新增 `subject_id` + 沿用 `chapter`（二维归属）；具体看 exp-1 recon 后定
- **AI 改编题视觉标识**：复用 finance spec v8 决策 #17-#21（adapted_payload 已存 `is_adapted` 字段），在 `QuestionCard.vue` 加 visual 徽章
- **预处理 2 种情况**：复用 finance pipeline 参数化 subject；第②种 Agent AI 出题用 minimax-cn-coding-plan 自身推理，不调外部 API
- **5 题型扩展**：简答 + 案例分析 后端如何记分 + UI 如何输入（参考 finance 的解析答按点给分逻辑，但要扩展更多输出形式）
- **远程仓库**：待 Phase 3 末定（用户没明说 git remote URL，必须在 Phase 4 前问）

## Next Steps

1. Phase 0 reconcile：exp-1 + ora-1 返回 → 我综合 plan V2 → 给用户确认或问 clarifications
2. Phase 1 派 fix-29 + des-2：实施题目章标识 + AI 改编视觉标识 → ora-1 phase 1 review → 修 actionable → 单元测试
3. Phase 2 派 exp-2（预处理资料结构）+ ora-2 phase 2 review（schema 风险）→ fix-30 + des-3（顶栏科目切换器）→ fix-31（预处理二种情况 + 5 题型扩展）→ oracle phase 2 review → e2e verify
4. Phase 3 派 fix-32（Docker 单容器化 + env.example + 排除敏感）+ memory profile 测试 → oracle phase 3 review
5. Phase 4 final review + git push remote（先问 remote URL）+ /comet-archive

## Critical Context

### 用户已收的 clarifications
- ✅ **远程仓库 URL**：`git@github.com:meisijiya/EggEgg_Examination_System.git`（GitHub，SSH）
- ✅ **Docker 数据位置**：保持 volume mount (`- ./data:/app/data`)
- ✅ **3 张图策略**：用户决策 = "Agent AI 生成后再由 Agent 审查答案，多 Agent 协作，可以上网搜索，确保高准确率"
- ✅ **AI 生成 review gate**：默认采用「AI multi-agent 协作 + 联网搜索 + /admin 入库前最终 manual review 100%」（oracle P0 critical 兜底保留）

### Phase 0 状态
- exp-1 完成：8 领域 recon 全部 covered（章节来源 / AI 标识 / 主题切换 / 5 题型 / 预处理参数化 / Docker / 公司战略资料 / 远程仓库）
- ora-1 完成：5-phase plan 6-section review + 8 actionable
- 关键发现：**前后端 schema 不同步**（api.ts 缺 is_adapted/source_question_id/chapter_id）；3 张图 find 无结果（推测 docx 内嵌图）；**ORM 层 subject/chapter 字段已存（零 schema migration 必需）**；Docker 已完整就绪（多阶段 / 单 service / 资源限制 / volume mount）

### Plan V2（精简 3-phase，oracle P1 采纳）
- **Phase 0** recon + clarifications ✅ done
- **Phase 1** Task 1 + Task 2 并行（4 background fixers）
- **Phase 2** oracle final review + git push + 排除敏感 + /comet-archive

### 任务原文（用户 3 段）
- § 1: 题目显示「第几章」（做题 + 查阅成绩都看）；混合模式 AI 改编题明显标识
- § 2: 前端顶「科目」选项；复用现有接口 + 前端设计；预处理分 ① 题目+答案成对 / ② 只有题或知识点（Agent AI 生成 + 折叠栏引用资料 + AI 生成标注）；3 张图（5942a7cb / e63d7f3d / 9662a07326）是简答题无答案归第②种；题型 单/多/判/简答/案例分析
- § 3: 跑通后内存不溢出 + DB 规范稳定 → docker 一键拉起（前后端 + DB + 预处理数据 + env.example）→ 排除敏感 → 提交远程仓库

### 用 deepwork skill 的理由
- 用户明说：长程任务每阶段都要委派审查
- 多 phase 实施（≥ 4 phase）+ 多 specialist 协作（oracle / fixer / designer / explorer）+ 必须每 phase oracle 审查
- 风险面：schema migration / Docker 内存 / 远程仓库敏感 / 5 题型扩展

### Background Job Board
- **Reusable**：ora-1（spec+backend+finance 上下文全）/ fix-18（backend 全栈 + ExamResult）/ fix-19（spec+paper_assembler+Home+types+api）/ des-1（UI 设计令牌 + 7 .vue）
- ora-1 reuse 已用于本次 plan review（新启 dispatch）

## Relevant Files

- 项目根：`/home/ljh2923/opencode-project/EggEgg_Examination_System/`
- spec v8：`docs/superpowers/specs/2026-07-04-finance-exam-system-design.md`
- finance 已 ship 进度：`.slim/deepwork/finance-exam-system-shipment.md`（239 行，Phase 0-3 archive-ready）
- 新科目资料：`@公司战略和风险管理/`（待 exp-1 探查）
- finance 预处理产物：`data/parsed/questions.jsonl` + `data/parsed/difficulty/ch{1..9}.jsonl` + `data/qa/oracle_review.md` + `data/qa/import_report.md`
- docker 现状：`deploy/Dockerfile` + `deploy/docker-compose.yml` + `deploy/nginx.conf`（待 exp-1 探查 + 拍 .env 是否已 .gitignore）
- 设计令牌：`packages/frontend/src/styles/{tokens,element-overrides,global}.css`
- 模型：`packages/backend/app/models/`（待 exp-1 探查 Question / Chapter / Examination schema）
- API：`packages/backend/app/api/{exams,questions,chapters,auth,admin}.py`（待 exp-1 探查）
- AI 客户端：`packages/backend/app/services/deepseek_client.py`
- AI 改编：`packages/backend/app/services/adapt_service.py`（已存 `adapted_payload_json` + `is_adapted`）
- 题型支持：finance spec v8 已含 single/multi/judge/calc/comprehensive（待 exp-1 探查 Question 模型）
- 服务端 entry：`packages/backend/app/main.py`
- Vue 页面：`packages/frontend/src/pages/{Home,ExamPlay,ExamResult,Dashboard,Admin}.vue`
- 题目卡片：`packages/frontend/src/components/QuestionCard.vue`
- 测试：130 backend + 41 frontend（finance）
- 远程仓库 URL：**待问用户**（Phase 4 前必填）

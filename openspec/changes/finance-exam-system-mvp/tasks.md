# Tasks: Finance Examination System MVP

## Phase 1 — 后端 MVP（highest priority）

- [x] T1.1 项目结构 — `packages/backend/` 初始化（pyproject.toml / app/ / alembic/ / tests/）
- [x] T1.2 配置层 — `.env.example` 完善；pydantic-settings 读环境变量
- [x] T1.3 数据访问层 — SQLAlchemy 2 models（subjects / chapters / questions）+ 异步 session
- [x] T1.4 Auth 模块 — 单密码 JWT（`/auth/login`），独立 admin 密码
- [x] T1.5 出题 Service — `paper_assembler.py`（按 spec §6.2 章节×题型×难度三维加权抽样）
- [x] T1.6 判分 Service — `grader.py`（客观对照 + 关键词覆盖率，按 spec §6.4）
- [x] T1.7 数据模型补充 — `exam_attempts` + `attempt_answers` 表 + Alembic 迁移
- [x] T1.8 测试 — pytest 全覆盖：解析器 / 抽题算法 / 判分阈值 / API 集成（mock LLM）
- [x] T1.9 OpenAPI 文档 — FastAPI 自动生成 + README 引用

## Phase 2 — Web API 端点

- [x] T2.1 `POST /auth/login` — 单密码 JWT
- [x] T2.2 `POST /exams/start` — 启动模拟考（algorithm→paper）+ 写 exam_attempts
- [x] T2.3 `GET /exams/{id}` — 拉取试卷（断线重连）
- [x] T2.4 `POST /exams/{id}/submit` — 交卷 + 判分 pipeline（同步等待 ≤ 2s）
- [x] T2.5 `GET /exams/{id}/result` — 完整成绩详情（含每题评语）
- [x] T2.6 `GET /dashboard` — 历次成绩 + 趋势 + 章节雷达数据
- [x] T2.7 `POST /exams/{id}/explain` — 占位 stub（DeepSeek 客户端待后续接入）
- [x] T2.8 `GET /admin/review/queue` + `POST /admin/review/questions/{id}` — 开发期人工 review（独立鉴权）

## Phase 3 — 前端 SPA

- [ ] T3.1 Vue 3 + Vite + TS 项目初始化（`packages/frontend/`）
- [ ] T3.2 Element Plus 集成 + 中文 locale
- [ ] T3.3 路由 — Vue Router（/login / /exam/:id/* / /dashboard / /admin）
- [ ] T3.4 状态管理 — Pinia（auth / exam / dashboard 三模块）
- [ ] T3.5 `/login` 页 — 单密码表单 + JWT 持久化
- [ ] T3.6 `/` 首页 — 最近成绩 + "开始模拟考"按钮
- [ ] T3.7 `/exam/:id/intro` 页 — 考试介绍（时长/分值/题型分布）
- [ ] T3.8 `/exam/:id/play` 答题页 — 分题型卡片切题 + 计时器 + 提交前校验
- [ ] T3.9 `/exam/:id/result` 成绩页 — 总分 + 章节雷达 + 每题评语 + AI 讲解按钮
- [ ] T3.10 `ExplainPanel.vue` — 流式接收 SSE 讲解内容
- [ ] T3.11 `/dashboard` — ECharts 趋势折线 + 章节雷达 + 历次列表
- [ ] T3.12 `/admin` 页 — 题目 review 独立登录（仅开发期）
- [ ] T3.13 前端测试 — Vitest：答题卡片 / 计时器 / 雷达图 / 讲解面板

## Phase 4 — 部署

- [ ] T4.1 `Dockerfile` 多阶段（Python 后端 + Node 前端构建）
- [ ] T4.2 `docker-compose.yml` — 单容器 + data 挂载 + .env
- [ ] T4.3 `healthcheck` — `/health` 端点 + curl 检查
- [ ] T4.4 反代配置示例 — `nginx.example.conf` (HTTPS, WebSocket, gzip)
- [ ] T4.5 `.env.example` 完整版本
- [ ] T4.6 README 写部署流程（云服务商反代 + docker-compose up）

## Phase 5 — 自测 & 上线

- [ ] T5.1 本地端到端测试 — 启动 → 模拟考一遍 → 检查数据持久化
- [ ] T5.2 部署到云服务器（用户执行）
- [ ] T5.3 域名 + TLS（云服务商反代）
- [ ] T5.4 女友可用性测试 + 反馈迭代

## Phase 6 — Verify & Archive

- [ ] T6.1 `/comet-verify` 烟测（spec 一致性 / 测试覆盖 / 文档完整）
- [ ] T6.2 spec v6 → v7 同步（如有偏差 — 主要修 difficulty 字段为 INTEGER）
- [ ] T6.3 设计文档 + plan + spec 一起 commit
- [ ] T6.4 `/comet-archive` 收尾（delta→main spec sync + 标记 archived）

---

**预估工时**: 后端 MVP ~5-8 小时（含测试）；前端 ~5-7 小时；部署 ~1 小时；自测 ~1 小时。

**并行性**:
- T1.1-T1.4 (后端基础) 与 T3.1-T3.4 (前端基础) 可并行
- T1.5-T1.9 (Service + 测试) 与 T3.5-T3.13 (UI 页面) 可并行
- T4 部署可与 T5 自测并行准备

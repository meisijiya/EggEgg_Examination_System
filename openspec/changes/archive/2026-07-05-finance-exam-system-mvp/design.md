# Design: Finance Examination System (MVP)

**链接**: 完整设计见 `docs/superpowers/specs/2026-07-04-finance-exam-system-design.md`（v7）

## 高层架构决策

### 1. 运行时 LLM 边界
- **DeepSeek / 外部 LLM = 仅 AI 讲解**（运行时 + 学员主动触发）
- **出题 / 判分 / 难度评估 = 零外部 LLM**（开发期由 Agent 团队 AI 评估；运行期纯算法 + 规则）
- 关键不变量：单次模拟考完成 = 0 次外部 LLM 调用

### 2. 三段式 Pipeline
```
预处理数据（已入库）
    ↓
(运行时) 纯算法抽样 → 模拟考试卷
    ↓
纯规则判分 → 客观题对照 + 主观题关键词覆盖率
    ↓
学员按需触发 → AI 讲解（流式 SSE，仅错题或主动请求）
```

### 3. 数据流
```
浏览器 (Vue 3 + Element Plus + ECharts)
    ↓ HTTPS
FastAPI 单进程 (Uvicorn)
    ↓           ↓           ↓
SQLite      预处库 JSON   DeepSeek Client（仅讲解）
(运行时)
    ↓
data/final/finance.db (已构建，3 表 565 题)
```

## 关键技术选型

| 层 | 选型 | 理由 |
|---|---|---|
| 后端 | Python 3.11+ FastAPI + Uvicorn | LLM 流式 + async 友好 |
| ORM | SQLAlchemy 2.0 + Alembic | 类型化 + 可迁移 |
| 数据库 | SQLite WAL | 零运维 + JSON 字段 |
| LLM | `openai` Python 库（兼容 DeepSeek） | base_url 切换 |
| 校验 | Pydantic 2 | 严格模式 |
| 前端 | Vue 3 + Vite + TypeScript | 上手快 |
| UI | Element Plus | 中文文档齐 |
| 图表 | ECharts | 趋势图 + 雷达图 |
| 部署 | Docker Compose 单容器 | 一份配置 |

## 数据模型（spec §7 已实现）

3 张表：
```sql
subjects(id, name)
chapters(id, subject_id, code, title, weight)
questions(id, subject_id, chapter_id, type, difficulty, stem, options_json, answer, key_points_json, analysis, source_pdf, page_ref)
```

`exam_attempts` / `attempt_answers` 运行时建（每场模拟考）。

## 关键模块设计

### A. 出题算法（spec §6.2）
- 输入：`paper_spec` + `distributions.json` + SQLite 题库
- 按章节 × 题型 × 难度 三维加权随机抽样
- 难度权重按 `difficulty_target = {easy:0.3, medium:0.5, hard:0.2}` 补偿偏易分布
- 缺口 fallback 到同题型相邻难度
- 目标：单次模拟考 ≤ 500ms 出题完成

### B. 判分算法（spec §6.4）
- **客观题**: 选项集合对比（单选/多选/判断）
- **主观题**: 关键词覆盖率 = (matched_key_points / total_key_points) × max_score
  - 60% 阈值起步给分（可调 `.env` MIN_COVERAGE）
  - 答案 < 5 字 → 0 分
- 学员答案空白 → 0 分 + 未作答评语

### C. AI 讲解（spec §6.6）
- 学员主动点击"AI 讲解"按钮
- 后端调 LLM 流式返回 JSON（title + explanation + missed_points + study_tip）
- SSE 流式推到前端，解释面板逐字渲染
- 失败回退：显示"参考答案 + 解析"（数据库有）

### D. 历史成绩 / Dashboard
- `exam_attempts` 表 + `attempt_answers` 表
- GET `/dashboard` 返回：历次成绩 + 章节分聚合 + 时间趋势
- ECharts 渲染：折线（趋势）+ 雷达（章节强弱）

## 部署结构

```
finance-exam-system/
├── packages/
│   ├── preprocessor/        # 已完成（parse_questions, build_db）
│   ├── backend/             # 待开发（FastAPI）
│   └── frontend/            # 待开发（Vue 3 SPA）
├── deploy/
│   ├── docker-compose.yml   # 单容器
│   ├── Dockerfile           # Python + Node 多阶段
│   └── nginx.example.conf   # 反代配置示例
├── data/                    # 数据资产（已入库）
│   ├── parsed/
│   ├── difficulty/
│   ├── qa/
│   └── final/finance.db
└── docs/
    └── superpowers/
        ├── specs/2026-07-04-finance-exam-system-design.md
        └── plans/...
```

## 关键边界

| 边界 | 体现 |
|---|---|
| 运行时 LLM 边界 | DeepSeek 仅 AI 讲解模块调用 |
| 外部依赖最小化 | 不引入 Redis / Postgres / Celery / K8s |
| 失败容忍 | 讲解失败回退；题库不足同章节同题型替换 |
| 数据安全 | .env 不入 git；DB 定时备份；admin 密码二次校验 |

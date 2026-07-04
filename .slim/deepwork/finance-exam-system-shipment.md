# Deepwork: finance-exam-system 端到端交付

> **Task**: 优化好的前端 → 委派审查 → 全面跑通 → 确保稳定 → 可交付
> **Started**: 2026-07-05
> **Owner**: orchestrator (用户 + Agent 团队)
> **Spec anchor**: `docs/superpowers/specs/2026-07-04-finance-exam-system-design.md` (v6/v7)

---

## 当前状态（2026-07-05 01:25）

### Active Background Jobs

| Task ID | Lane | 范围 | 状态 |
|---|---|---|---|
| `ses_0d1ef1f4effe5Txd9DTg5Ct2Wt` (fix-19) | mixed mode UX | 修 client.ts timeout 分级 + token 防御 + 401 友好化 + Login.vue expired 参数 | **running** |
| `ses_0d1d17d0affeKAqmyErO2fjjYY` (des-1) | 前端 UI 优化 | 雪天/蓝天/旭日 设计令牌 + Element Plus 主题 + 7 页 + 2 组件 + App.vue 顶层 | **running** |

按 deepwork 调度纪律：**不 poll**；hook 通知 reconcile 后再 advance。

### 已有交付（已 reconciled）

- `fix-12` 后端 MVP（T1+T2, 62 测试 82% 覆盖）✅
- `fix-13` 计算分析题输入框 bug（@input 改 @update:model-value）✅
- `fix-14` 部署配置（Dockerfile + compose + nginx）✅
- `fix-15` Phase 5 整合（StaticFiles + SPA fallback）✅
- `fix-16` AI 讲解真实 SSE 接入（SSE_BASE 修复）✅
- `fix-17` 答案按点给分 + 编号格式拆分（parse_sub_answers 三档）✅
- `fix-18` Dashboard 删除 + 时区 + 模式 UI + result bug fix + exams.py 改造 ✅
- `fix-19` (含) AI 混合出题 + adapt_service + 防幻觉 prompt（adapted 0% bug 已 inline 修）✅

### 数据资产（preprocess pipeline 完成）

- `data/parsed/questions.jsonl` — 565 题（Pydantic 严格校验通过）
- `data/parsed/difficulty/ch{1..9}.jsonl` — 9 章难度评估
- `data/qa/oracle_review.md` — P3 审查（一致率 88.9%）
- `data/final/finance.db` — SQLite (256KB, subjects=1, chapters=9, questions=565)
- tmux session `fes` — uvicorn 在跑（pid 动态）

### 关键 commit

- `0e56c55` — comet-open artifacts
- `86fe0a2` — data(P1-P4) preprocess pipeline
- (post fix-13/16/17/18) — npm build + 后端 inline 修

---

## Phased Implementation Plan（**Oracle 简化版**）

### Phase 1 — Reconcile + spec sync（合并）
- reconcile fix-19 + des-1 terminal results
- 后台 task: spec v6 → v8 同步（含所有 fix 回溯）
- **P0 #1 派 fix-25**：修 `adapted_payload_json` 列缺失 + asyncio.gather 并行改编（critical bug）
- @oracle review spec diff
- Designer Handoff Guardrail 显式 diff check：des-1 产物中 Dashboard.vue / Login.vue 是否保留 fix-18 改的删除按钮和 fix-19 改的 ?reason=expired

### Phase 2 — End-to-end verify（用更新后 spec 做 oracle 标尺）
- 后台 task: 跑 full E2E 脚本（合并原 Phase 2 + Phase 4 端到端 verify）
  - standard 模式启动（< 1s 出题）
  - **mixed 模式验证 adapted_answer 判分正确**（fix-25 必修）
  - 答题 → 交卷 → 判分（关键词覆盖率 + 编号格式 + adapted_answer）
  - AI 讲解真实 SSE 流式
  - Dashboard 趋势 + 雷达 + 删除按钮
  - 时区 Asia/Shanghai
  - 401 → /login?reason=expired 闭环
- @oracle review E2E 测试结果

### Phase 3 — Commit hygiene + archive
- **git 收尾**：把 fix-12 ~ fix-25 的 untracked 代码 staged commit（每个 fix 独立 SHA）
- `/comet-archive`：delta spec → main spec sync + design doc / tasks.md 标 archived
- @oracle final review

---

## Open Questions / Risks

1. **mixed mode 启动慢** — 30 题 × DeepSeek 5-10s = 30-60s 启动。需要缓存 / 预热 / 减少 %？
2. **adapted_payload_json 列缺失** — fix-19 提示。学员在 mixed 模式改编题作答时是否需要用 adapted_answer 判分？
3. **Dashboard 删除按钮是否在 UI 优化中被破坏** — des-1 优化样式时可能误删功能（des-1 task 显式要求保留 fix-18 改的逻辑）
4. **Login.vue 改样式是否破坏 fix-19 的 expired URL 提示** — fix-19 加 ?reason=expired 参数

---

## Deepwork Designer Handoff Guardrail

des-1 完成后：
- 保留 .appframe / .qcard / .stat / .tag.sky 等设计令牌
- 后续 phase 改样式只能用 @fixer（mechanical only）
- 视觉/交互变更必须 route 回 @designer
- **绝不允许** @fixer 修改 des-1 沉淀的视觉决策

---

## Confirmed Research Context（reusable for @oracle / @designer reviews）

- **spec v6**：`docs/superpowers/specs/2026-07-04-finance-exam-system-design.md`
- **codebase 状态**：`git log --oneline -10` + `ls packages/{backend,frontend}/`
- **设计令牌来源**：`frontend-UI/index.html`（雪天/蓝天/旭日 oklch + Element Plus 主题覆盖）
- **DeepSeek 集成**：`packages/backend/app/services/deepseek_client.py`（OpenAI 兼容 SSE + chat_json_async）
- **adapt 护栏**：`packages/backend/app/services/adapt_service.py`（3 重护栏：type / key_points / 答案等价）
- **判分核心**：`packages/backend/app/services/grader.py`（parse_sub_answers 三档 + 关键词覆盖率）
- **uvicorn 状态**：tmux session `fes` 在跑
- **build 工具**：Vue 3 + Vite + TS + Element Plus 2.5 + ECharts 5 + FastAPI + SQLAlchemy 2 + SQLite

> 按 deepwork 规则：路径引用，不复制文件内容。

---

## Phase Updates（时间倒序）

### 2026-07-05 01:25 — Phase 0 完成
- 创建 `.slim/deepwork/` 目录 + `.ignore` + `.gitignore` 规则
- 起草 plan (5 phases)
- 派 @oracle review plan (reuse ora-1 session) — running
- 等 fix-19 + des-1 hook 完成 → reconcile → Phase 1 advance

### 2026-07-05 01:32 — fix-19 reconciled（inline 任务，不属 deepwork plan Phase）
- **范围**：mixed mode timeout 15s + 401 Not authenticated
- **结果**：6 个前端文件改 + 10 新测试 → 41/41 tests pass，npm build 成功
- **关键交付**：axios timeout 180s + 401 → `/login?reason=expired` 闭环
- **end-to-end curl**：mixed mode 201 Created + 41 题（180s timeout 足够覆盖 144-204s）

### ⚠️ fix-19 报告 → **Phase 2 必处理的 actionable issues**（1 个 critical + 1 个 minor）

#### Critical: `is_adapted` 字段未透传前端（影响 mixed mode 判分准确性）
- **现象**：`exams.py public_qs` 序列化白名单只取 `id/type/chapter/stem/options/score/sequence`
- **缺失字段**：`is_adapted` / `source_question_id` / `adapted_answer` / `adapted_key_points` / `adapted_analysis`
- **后果**：学员在混合模式改编题上作答时，判分用 DB 原题答案（不是 `adapted_answer`）→ **可能误判**
- **修复方向**（**Phase 2 端到端 verify 时一并修**）：
  1. `exams.py start_exam` 的 `public_qs` 构造：增加 `is_adapted` / `source_question_id` / `adapted_answer` 等字段透传
  2. `attempt_answers` 表加 `adapted_payload_json` 列（Alembic 迁移 + SQLAlchemy 模型）
  3. `grader.py` 判分时检测 `is_adapted` 改用 `adapted_answer` 判分
  4. mixed mode 重启 uvicorn 后 verify adapted_count > 0
- **风险**：`exams.py` + `attempt.py` (model) + `grader.py` 涉及多个文件，需派 background fixer 一次性修
- **谁改**：派 background fixer 修 + @oracle review
- **deadline**：Phase 2 端到端 verify 时（不可拖延到 Phase 3+，因为判分准确性 critical）

#### Minor: 测试覆盖 mixed mode 改编题判分
- 现有测试 41/41 是 standard mode 覆盖
- 缺 mixed mode 改编题判分测试（用 `adapted_answer` 而非原题答案）
- **修复方向**：Phase 2 verify 时加 `tests/test_mixed_grader.py` 覆盖

### Phase 0.5 — fix-19 reconciled，待 ora-1 + des-1 完成才 advance Phase 1

| Task ID | 状态 |
|---|---|
| fix-19 (修 mixed mode timeout + 401) | ✅ **reconciled** |
| ora-1 (审查 5-phase plan) | running |
| des-1 (前端 UI 全面优化) | running |

🚫 **不 poll ora-1 + des-1**。等 hook-driven completion → reconcile → advance Phase 1。

### Phase 0.6 — ora-1 reconciled + plan 调整（3 phase 简化）+ P0 actionable 派发

- ora-1 揭示 3 P0 阻塞：
  1. `adapted_payload_json` 列缺失（混合模式判分用错答案）→ 派 fix-25（reuse fix-18 session）
  2. asyncio.gather 并行改编（60-96s 串行 → < 30s 并行）
  3. Phase 顺序调整：spec sync 必须在 verify 之前
- 采纳 simplify：6 phase → 3 phase
- P0 actionable 全部派发

### Phase 0.7 — des-1 reconciled，designer 视觉意图沉淀

**des-1 完整交付**：
- 3 个新 CSS（tokens.css 114 行 / element-overrides.css 63 行 / global.css 407 行）
- 10 个 .vue 只改 `<style>` + 极少 script 必要项
- npm run build 成功（dist 2.1MB）
- 41/41 tests pass（**未破坏**）
- vue-tsc 类型检查通过

**Designer Handoff Guardrail 验证**（oracle P3 风险全部 cleared）：
- ✅ Dashboard.vue 删除按钮（fix-18 逻辑）保留：`handleDelete` + `formatDateTime` 未碰
- ✅ Login.vue `?reason=expired` ElMessage（fix-19 逻辑）保留
- ✅ QuestionCard.vue 事件处理（fix-13 修复）保留：`@click="toggleOption"` + `@update:model-value="onTextInput"` 未碰
- ✅ tokens 设计令牌严格从 `frontend-UI/index.html` 第 11-84 行复制
- ✅ Element Plus 主题全部走 hex（spec 第 616-618 行 SCSS 约束）

**沉淀的设计意图**（后续 phase 改样式必须保留）：
- 配色：雪 `oklch(98.5% 0.006 230)` / 蓝天 `oklch(64% 0.12 232)` / 旭日 `oklch(80% 0.10 60)`
- 类名约定：`.qcard` `.option` `.btn-*` `.timer.safe/warn/danger/pulse` `.progress` `.feedback.ok/no` `.stat` `.tag.sky/sun/success/warning/danger` `.note` `.input/.textarea` `.topnav` `.appframe` 等
- 圆角节奏：xs 4 / sm 6 / md 10 / lg 14 / xl 20 / pill 999
- 阴影：低饱和 OKLch，不用纯黑；sky/sun 主题色阴影
- 8 色 chart palette（chart-1 ~ chart-8）

### 当前等待

| Task ID | 状态 |
|---|---|
| fix-19 (mixed mode timeout + 401) | ✅ reconciled |
| ora-1 (plan 审查 + 风险识别) | ✅ reconciled |
| des-1 (前端 UI 全面优化) | ✅ **reconciled** |
| fix-18 / fix-25 (P0: adapted_payload_json + asyncio.gather) | running |

🚫 **不 poll fix-18/fix-25**。等 hook-driven completion → reconcile → advance Phase 1（reconcile code + spec v8 sync + commit hygiene）。

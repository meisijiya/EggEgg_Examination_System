# 📚 新科目 onboarding 流程

> 接手 onboard 新科目(继 `fin-mgmt` / `corp-strat` 之后)的开发者模板。
> 详细架构见 [`spec §5`](superpowers/specs/2026-07-04-finance-exam-system-design.md) + [`spec §14.1`](superpowers/specs/2026-07-04-finance-exam-system-design.md)。

---

## 1. 📂 资料准备 — 三类输入

| 输入类型 | 工具 | 适用科目 |
|---|---|---|
| **PDF(题目+答案成对)** | [`parse_questions.py`](../packages/preprocessor/parse_questions.py) | fin-mgmt(已 onboard) |
| **DOCX(只有题目/知识点)** | [`parse_docx.py`](../packages/preprocessor/parse_docx.py) → multi-agent AI 出题 | corp-strat(已 onboard) |
| **图片(无 OCR)** | ❌ 不入题库 | 暂不支持, 先 OCR 转文本 |

**反面教材** ⚠️: `实证研究结构框架(1).docx` 是研究方法论文, **不属考试资料** → fix-24 加子串 noise 剔除, 写 `errors.log` 不入题库。

---

## 2. 🧮 算法出题 + 7 题型 dispatcher

- 入口: [`paper_assembler.py`](../packages/backend/app/services/paper_assembler.py) — `assemble(subject=None, mode='standard')`, 章节 × 题型 × 难度三维加权抽样
- 题库 < spec 要求 → `partial=True` 兜底(spec §6.2.1 critical fix), **不抛 RuntimeError**
- 题型白名单: `single / multi / judge / calc / comprehensive / short_answer / case_analysis`(`schemas.py` 枚举)
- 判分: `grader.grade_answer` 按题型 dispatcher — 客观题精确匹配 / `calc|comprehensive` 关键词覆盖率 / `short_answer` 不拆 sub / `case_analysis` rubric 逐项打分

---

## 3. 🤖 AI 讲解(`deepseek_client.py`)

- 触发: 结果页 "AI 讲解" → `POST /exams/{id}/explain` 流式 SSE
- **fallback 行为**(用户硬约束): `DEEPSEEK_API_KEY` 缺失/失败 → "参考答案 + 解析", 不影响考试

---

## 4. 🔁 Multi-agent AI 出题(4-stage)

适用于 **DOCX 资料**(无现成答案) — 以 `corp-strat` 为蓝本:

1. **Question Generation** — DOCX 段落 → 结构化 Question
2. **Web Search Grounding** — DuckDuckGo HTML(无 API key)验证 key_points
3. **Answer + Key_points Synthesis** — 综合 DOCX + 联网
4. **Peer Review** — 第二 LLM 视角一致性; 不一致 → `needs_manual_review=true`

入口: `corporate_strategy_q_gen.multi_agent_q_gen()`(Semaphore 限流) → `corporate_strategy_ai_generated.jsonl`

---

## 5. 🚦 Admin review gate(用户硬约束)

> **入库前 100% 人工 review** — 用户决策, oracle P0 critical 兜底。

- API: `GET /admin/ai-generated-questions?status=pending` + `POST /admin/approve-question/{id}`
- 自动 fallback: 用户不在 `/admin` 时, 跑 [`auto_approve_ai.py`](../packages/backend/scripts/auto_approve_ai.py) — 按 `confidence + needs_manual_review` 决策
- **`build_db.py` 加载规则**: 只认 `status='approved'` 入 SQLite 题库

---

## 6. 🐳 数据持久化(双层)

- **build-time**: `data/parsed/`, `data/distributions/`, `data/qa/` → COPY 入 image
- **runtime VOLUME**: `./data:/app/data:rw` 暴露 `finance.db` / `app.db`

详见 [`deploy/OPERATIONS.md` §2](../deploy/OPERATIONS.md)。

---

## 7. 📋 已 onboarded 科目

| Subject ID | 中文名 | 题目量 | 状态 |
|---|---|---|---|
| `fin-mgmt` | 财务管理 | 565 | ✅ 部署运行 |
| `corp-strat` | 公司战略和风险管理 | 47 | ✅ 部署运行(清理 16 条 AI 残题后) |

**新科目 checklist**:
- [ ] parse 适配新目录 + chapter 白名单
- [ ] `subjects/chapters` 表 INSERT
- [ ] `build_db.py --subject` 多库输出
- [ ] 前端 SubjectSwitcher 加下拉 + `paper_assembler` partial-fill 单测

> 📚 `spec §5.1`(预处理) / `spec §6`(运行时) / `spec §14.1`(新学科) / `README.md`
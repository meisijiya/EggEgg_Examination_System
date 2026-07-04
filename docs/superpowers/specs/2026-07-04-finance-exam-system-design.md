# 财务管理考试系统 — 设计 Spec

| 字段 | 值 |
|---|---|
| **项目代号** | `finance-exam-system`（简称 FES） |
| **作者** | Agent 团队（由 orchestrator 调度） |
| **审核者** | 项目所有者（用户本人） |
| **创建日期** | 2026-07-04 |
| **版本** | v5 |
| **状态** | 设计中（待 review） |
| **适用范围** | 当前 —— 财务管理科目；未来 —— 任何可解析 PDF 资料的科目 |

---

## 1. 概述

构建一个部署在远程云服务器的 Web 考试系统。当前学科为 **财务管理**，未来扩展其他学科。所有题目基于本地预处理资料（**资料本身就是题目+答案组合**），通过 **离线预处理 + 运行时纯算法抽样 + AI 讲解** 三段式 pipeline 完成。**不引入 RAG**（用户在需求阶段明确决策）。

### 1.1 设计目标

1. **覆盖广**：题库覆盖财务管理的 9 章 × 5 种题型，每章节每题型有可复用原题 ≥ 3 道；出题算法保证每次模拟考的章节覆盖完整
2. **拿高分**：模拟考贴近中级会计 / 财经类考试标准结构（100 分 / 120 分钟 / 41 题），所有题目带章节归属，错题 / 低分题触发 AI 讲解
3. **运行极速**：纯算法抽样出题 + 关键词匹配判分，单次模拟考完成**零 LLM 调用**；LLM 仅在 AI 讲解时按需介入
4. **数据可信**：开发期由 **Agent 团队多轮迭代审查 + 用户人工全量 review** 把关入库；运行时 99% 信任预处理数据（DeepSeek 仅做讲解，不参与任何判断/出题）
5. **单人单实例运维**：女友使用，无多租户、无复杂认证、无外部依赖（DB、Redis、Celery 都不引入），一份 `docker-compose up` 即可
6. **判分零 LLM**：判分模块不依赖 LLM，关键词覆盖率算法本地完成，零误判风险、零延迟、零成本

### 1.2 明确非目标（YAGNI 清单）

- ❌ 多用户 / 多租户 / 公开注册
- ❌ 章节刷题模式 / 错题本 / 自由组卷
- ❌ 移动 App / PWA
- ❌ WebSocket / 实时协作
- ❌ K8s / 微服务拆分
- ❌ RAG / 向量数据库
- ❌ 多模型路由 / AB 测试
- ❌ LLM 出题改编 / LLM 主观题评判

---

## 2. 用户画像与场景

| 项 | 值 |
|---|---|
| **真实用户** | 1 人（女友） |
| **使用设备** | 任意带浏览器的设备 |
| **使用频次** | 备考期间 1-3 次/周 |
| **网络** | 国内（云服务器国内节点） |
| **关键体验目标** | 「点击开始 → ≤ 3 秒看到试卷」+「交卷后 → 立刻拿到完整成绩」+「错题可一键触发 AI 讲解」 |
| **失败容忍** | 错题 / 解析缺失 = 学习失败；判分必须无 LLM 失误 |

**运维边界**：女友无需任何运维能力；只有项目所有者（用户）会做配置（DeepSeek API key、管理员密码）。

---

## 3. 技术栈

| 层 | 选型 | 关键理由 |
|---|---|---|
| 后端语言 | Python 3.11+ | LLM SDK / PDF 解析生态成熟；async 对 LLM 讲解长调用友好 |
| Web 框架 | FastAPI + Uvicorn | StreamingResponse 支持 AI 讲解流式输出 |
| ORM | SQLAlchemy 2.0 + Alembic | 迁移可控；类型化查询 |
| 数据库 | SQLite（WAL 模式） | 零运维；单文件易备份；JSON 字段支持关键词数组 |
| LLM SDK | `openai` Python 库（DeepSeek 兼容 OpenAI API） | 一行 `base_url` 切换，仅服务于讲解模块 |
| PDF 解析 | `pdfplumber` | 中文文本 + 坐标识别 |
| Schema 校验 | Pydantic 2 | 严格模式 |
| 前端 | Vue 3 + Vite + TypeScript | 上手快；组件库成熟 |
| UI 组件 | Element Plus | 中文文档齐；表格 / 表单齐全 |
| 图表 | ECharts | 趋势图 + 雷达图 |
| 流式消费（前端） | `fetch` + ReadableStream | 不需 EventSource 第三方库 |
| 测试（后端） | pytest + httpx | 异步 + 流式测试方便 |
| 测试（前端） | Vitest + Vue Test Utils | 标准搭配 |
| 部署 | Docker Compose 单容器 | 一份配置、一键启动 |

---

## 4. 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│  浏览器 (Vue 3 SPA + Element Plus + ECharts)                │
│  - 答题界面                                                  │
│  - 成绩页（含"AI 讲解"按钮 → 流式展示）                      │
└──────────────────────────────┬──────────────────────────────┘
                               │ HTTPS（云服务商反代）
                               ▼
┌─────────────────────────────────────────────────────────────┐
│  FastAPI 单进程 (Uvicorn)                                    │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐     │
│  │ 试卷生成 │  │ 答题批改 │  │ 成绩历史 │  │ AI 讲解  │     │
│  │ Service  │  │ Service  │  │ Service  │  │ Service  │     │
│  │ (纯算法) │  │ (纯规则) │  │          │  │(SSE 流式)│     │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘     │
│       └──────────────┴─────────────┴─────────────┘           │
│                              │                               │
│       ┌──────────────────────┼──────────────────────┐       │
│       ▼                      ▼                      ▼       │
│  ┌─────────┐           ┌──────────┐           ┌─────────┐   │
│  │ SQLite  │           │ 预处产物  │           │ DeepSeek│   │
│  │(考试/答案│           │(jsonl/   │           │ v4-flash│   │
│  │/成绩)   │           │ json)    │           │   API   │   │
│  └─────────┘           └──────────┘           └─────────┘   │
└─────────────────────────────────────────────────────────────┘
                                ▲
                                │ 一次离线运行（开发期）
                                │
                ┌──────────────────────────────┐
                │  预处理脚本 (preprocess.py)   │
                │  PDF → 题目+答案 → 题目库     │
                └──────────────────────────────┘
                                ▲
                                │ Agent 团队多轮审查
                ┌──────────────────────────────────┐
                │  @oracle (架构 + 财务逻辑)        │
                │  @explorer (数据完整性扫描)      │
                │  用户 (全量人工 review)           │
                └──────────────────────────────────┘
```

### 4.1 关键架构原则

| 原则 | 体现 |
|---|---|
| **运行时 LLM 边界** | DeepSeek 仅在"AI 讲解" Service 中被调用；出题 / 判分模块不引入 LLM |
| **预处理不调 LLM** | 开发期只用 Agent 团队 + 用户 review 兜底质量 |
| **失败容忍** | LLM 讲解失败回退到"显示参考答案 + 解析"；不影响考试本身 |

---

## 5. 资料预处理 Pipeline（开发期一次性）

> 关键性质：**资料本身就是题目+答案的组合**。预处理任务 = 结构化提取 + 质量校验，**不是**概念抽取 + 题目合成。

### 5.1 Pipeline 总览（5 阶段，零 LLM 调用）

```
┌─────────────────────────────────────────────────────────────┐
│ ① PDF → 纯文本 + 坐标 (pdfplumber)                         │
│    产出: data/raw/*.txt + layout.json                       │
│    门:   文本长度校验 / 章节标题检出率                       │
└──────────────────────────────┬──────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ ② 纯文本 → 题目对象 (Python 解析器，规则 + 启发式)         │
│    产出: data/parsed/questions.jsonl                         │
│    字段: id / type / chapter / number / stem / options /    │
│          answer / key_points[] / analysis / difficulty      │
│          / source_pdf / page_ref                             │
│    门:   Pydantic 严格校验（字段全、类型对、答案∈选项集）   │
│    ★ 本阶段对"计算分析题 / 综合题" 标记 key_points[]，       │
│      作为运行时关键词覆盖率判分的依据                         │
└──────────────────────────────┬──────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ ③ 章节×题型概率分布表 (离线计算)                            │
│    产出: data/distributions/finance.json                    │
│    内容: 章节权重、题型目标数、保护性约束                    │
│    运行时按此表抽样出题                                       │
└──────────────────────────────┬──────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ ④ 强质量校验 (纯规则 + 统计 + 抽样)                         │
│    a) 覆盖率自检: 9 章 × 5 题型 ≥ 3 题                       │
│    b) 答案合理性: 选项唯一、答案∈选项集合                     │
│    c) key_points 检查: 计算/综合题 ≥ 3 个要点               │
│    d) 解析抽样比对: 随机 50 道 vs 原 PDF 反向校验             │
│    产出: data/qa/qa_report.md                               │
└──────────────────────────────┬──────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ ⑤ Agent 团队多轮迭代审查 + 用户人工全量 review（开发期）     │
│    ★ 本阶段是预处理质量兜底的唯一环节（不调 LLM）             │
│                                                             │
│    迭代 1 — 结构化校验：                                      │
│      @oracle (架构) + @explorer (数据完整性)                  │
│      → schema / 覆盖率 / 抽样比对 / key_points 完整性          │
│                                                             │
│    迭代 2 — 领域逻辑审查：                                    │
│      @oracle (财务领域) 抽 30-50 道题                            │
│      → 答案合理性 / 解析完整性 / 章节归属 / key_points 准确性      │
│                                                             │
│    迭代 3 — 跨章节一致性 + 解析深度：                          │
│      @oracle (题库整体质量)                                   │
│      → 模糊答案 / 解析空题 / 孤立题 / key_points 表达精确度        │
│                                                             │
│    最终 — 用户人工全量过目：                                    │
│      /admin 路由（独立密码）                                     │
│      → 全部题目逐一确认 / 修正 / 加新题                             │
│                                                             │
│    每轮迭代产物：                                               │
│      data/qa/review_iter_<N>.md (发现问题 + 修复记录)            │
│      data/final/finance.db (最终入库, git commit SHA)            │
└─────────────────────────────────────────────────────────────┘
```

### 5.2 质量保障 4 大机制（开发期责任，不下放给运行时）

> **关键边界**：**DeepSeek 不参与开发期任何数据校验**——开发期只能由 Agent 团队（多轮迭代）+ 用户人工 review 兜底。LLM 评判对"它训练数据里也有的内容"未必能可靠识别错误，硬塞会引入假阳性。

| # | 机制 | 实现 | 防 |
|---|---|---|---|
| 1 | **Schema 硬校验** | Pydantic 2 严格模式 | 解析器默默吞错 |
| 2 | **覆盖率自检** | 9 章 × 5 题型 ≥ 3 题；key_points ≥ 3（计算/综合） | 章节漏题；判分失效 |
| 3 | **解析抽样比对** | 50 道随机题 vs 原 PDF 反向比对 | 解析偏移 |
| 4 | **Agent 团队多轮迭代审查 + 用户人工全量 review** | @oracle (架构+领域双轮) + @explorer (数据完整性) + 用户 `/admin` 全量过目 | 答案错 / 解析缺 / 章节误标 / key_points 表达模糊 |
| + | **版本化 + 可重跑** | git 管理 + 幂等脚本 | 不可逆改动 |

### 5.3 运行时抽样算法（章节×题型加权随机）

> 这是开发期交付给运行时的核心算法：保证每次模拟考的章节覆盖完整、题型比例正确、难度分布合理。

```python
def make_distributions(subject: str) -> Distribution:
    """离线计算一次，产出可被运行时消费的 probability table"""
    chapters = list_chapters(subject)
    bank = load_question_bank(subject)

    paper_spec = load_paper_spec()   # 单15 多10 判10 计4 综2 = 41 题

    chapter_prob = {}
    for ch in chapters:
        # 基础权重 = 该章可用原题数（题多 → 多出题）
        base_w = max(len(bank.by_chapter_type(ch, type_)), 1)
        chapter_prob[ch] = base_w * ch.user_weight    # 用户可调权重

    type_prob = {t: paper_spec[t] / sum(paper_spec.values())
                 for t in paper_spec}

    return Distribution(chapters, chapter_prob, type_prob)
```

**算法约束**：
- 一章被选中的概率 ∝ 该章可用原题数 × 用户权重
- 一题型被选中的概率 = 试卷规格中的题型占比
- 章节 × 题型组合时，**优先选取题量 ≥ 3 的题型**（保护性约束）
- 抽样保证**至少覆盖 9 章中的 8 章**（填空平衡机制）
- 单次模拟考章节分布 = `weighted_sample(population=chapters, weights=chapter_prob, k=41)`

---

## 6. 运行时：出题 + 判分 + 讲解 Pipeline

### 6.1 时序图（出题 + 判分 零 LLM）

```
  浏览器                              FastAPI
   │                                    │
   │  POST /exams/start                 │
   │ ──────────────────────────────────►│
   │                                    │  ① 查 distributions.json
   │                                    │  ② 章节加权抽样（41 槽位）
   │                                    │  ③ 按权重从预处库查候选
   │                                    │     ┌─ 客观题: 直接抽 1 道
   │                                    │     └─ 计算/综合: 抽 1 道 + key_points
   │                                    │  ④ 写入 exam_attempts + attempt_questions
   │ ◄─── 试卷 (id, questions[]) ──────│
   │                                    │
   │  ... 答题中（计时器服务端同步）...    │
   │                                    │
   │  POST /exams/{id}/submit           │
   │ ──────────────────────────────────►│
   │                                    │  ⑤ 客观题自动对照答案（is_correct, score）
   │                                    │  ⑥ 主观题关键词覆盖率对照
   │                                    │     - 提取学员答案 → 去停用词 → 比对 key_points
   │                                    │     - score = (matched / total) × max_score
   │                                    │  ⑦ 写入 attempt_answers (含每题 score)
   │                                    │  ⑧ 聚合成 total_score + score_by_chapter
   │ ◄─── 完整成绩 + 章节分 + 评语 ─────────│
   │                                    │
```

### 6.2 出题算法（核心，零 LLM）

```python
def assemble_paper(subject: str, paper_spec: PaperSpec) -> Paper:
    """主入口：纯算法 + 加权采样 + 按 key_points 标记主观题"""
    dist = load_distributions(subject)
    bank = load_question_bank(subject)

    slots = build_slots(paper_spec)         # 41 个空槽位（题型 + 分值）
    out: list[Question] = []

    for slot in slots:
        chapter = sample_chapter(dist, slot.type)   # 按权重加权采样
        candidates = bank.query(
            type=slot.type,
            chapter=chapter,
            difficulty=slot.difficulty_hint,
            limit=10
        )
        if not candidates:
            # 缺口：随机抽全章节兜底池（仍可保证出题）
            candidates = bank.query(type=slot.type, limit=20)

        out.append(candidates.random_one())   # 直接复用原题，零 LLM

    return Paper(questions=out, spec=paper_spec)
```

**关键不变量**：
- **算法主体 = 查表 + 加权采样 + 数据库随机抽一道**，完全无 LLM
- 单次模拟考生成耗时目标 ≤ 500ms

### 6.3 章节权重采样（含覆盖率保证）

```python
def sample_chapter(dist, q_type):
    """按 dist.chapters × weight 加权采样，保证覆盖广"""
    eligible = [
        (ch, ch.weight)
        for ch in dist.chapters
        if dist.has_enough(ch.id, q_type, min_n=3)   # 保护性约束
    ]
    if not eligible:
        return dist.fallback_chapter                  # 兜底
    return weighted_choice(eligible)
```

**8 章覆盖机制**：
- 算法在某章累计出现次数超过总题数 30% 时，强制降权；让后续抽样的章节尽量覆盖剩余章
- 9 章池，最少覆盖 8 章，章节不重复（除非题库不足）

### 6.4 判分算法（纯规则，零 LLM）

#### 6.4.1 客观题（单选/多选/判断）

```python
def grade_objective(question, user_answer) -> GradedAnswer:
    correct = set(question.answer.upper().replace(',', ''))
    given = set(user_answer.upper().replace(',', ''))
    is_correct = (correct == given)
    return GradedAnswer(
        score=question.full_score if is_correct else 0,
        is_correct=is_correct,
        comment="回答正确" if is_correct else f"正确答案：{question.answer}"
    )
```

#### 6.4.2 主观题（计算分析 / 综合题，关键词覆盖率）

```python
STOP_WORDS = {"的", "了", "是", "在", "和", "与", "或", "等", ...}  # 中文停用词表

def grade_subjective(question, user_answer, key_points: list[str]) -> GradedAnswer:
    """覆盖率达到 100% 给满分；60%~100% 按比例；< 60% 给 0 分"""
    if not user_answer or len(user_answer.strip()) < 5:
        return GradedAnswer(score=0, is_correct=False,
                            comment="答案过短，未达评判门槛")

    user_tokens = remove_stopwords(user_answer)
    matched = sum(
        1 for kp in key_points
        if any(tok in user_tokens for tok in tokenize(kp))
    )
    coverage = matched / len(key_points)
    if coverage >= 1.0:
        score = question.full_score
        comment = "完整覆盖所有关键要点"
    elif coverage >= 0.6:
        score = round(question.full_score * coverage, 1)
        comment = f"覆盖 {matched}/{len(key_points)} 个关键要点（{coverage*100:.0f}%）"
    else:
        score = 0
        comment = f"仅覆盖 {matched}/{len(key_points)} 个关键要点，未达 60% 门槛"

    return GradedAnswer(score=score, is_correct=score > 0, comment=comment)
```

**门槛可调**：默认 60% 起步给分；可在 `.env` 设 `MIN_COVERAGE=0.6` 调整。

### 6.5 异常处理（判分）

| 异常 | 处理 |
|---|---|
| 学员答案空白（任何题型） | `score = 0, comment = "未作答"` |
| 主观题字符 < 5 | `score = 0, comment = "答案过短，无法评估"` |
| 多选漏选 / 多选 | 选项集合对比，集合相等才算对 |
| key_points 为空（数据缺失） | 主观题退化为"按参考答案完全一致才得分"（兜底） |

### 6.6 AI 讲解模块（学员按需触发）

#### 6.6.1 触发场景

| 场景 | 谁触发 | 时机 |
|---|---|---|
| 看错题详解 | 学员点"AI 讲解" | 结果页 |
| 看对的题的拓展 | 学员点（可选） | 结果页 |
| 看不懂参考答案 | 学员点"再讲详细点" | 讲解展开后 |

#### 6.6.2 时序图

```
  浏览器                            FastAPI                          DeepSeek
   │                                  │                                  │
   │  POST /exams/{id}/explain        │                                  │
   │  { question_id, level="standard" │                                  │
   │    OR "detailed" }               │                                  │
   │ ────────────────────────────────►│                                  │
   │                                  │  构造 prompt: 题干 + 学员答案      │
   │                                  │    + 参考答案 + key_points + 章节  │
   │                                  │                                  │
   │                                  │  ──────────────────────────────► │
   │                                  │ ◄───── chunk 1 (流式) ────────────│
   │ ◄─── SSE: data: {...} ───────────│                                  │
   │ ◄─── SSE: data: {...} ───────────│ ◄───── chunk 2 ─────────────────│
   │   ...                             │   ...                            │
   │ ◄─── SSE: [DONE] ────────────────│ ◄───── chunk N ─────────────────│
   │                                  │                                  │
```

#### 6.6.3 System Prompt（讲解唯一 1 套）

```
你是财务管理助教。请针对学员的作答给出讲解。

【背景】
学员答案可能正确也可能错误，正确率由关键词覆盖率算法已判完成，
本题得分 = ${score}/${max_score}（${is_correct_or_not}）。

【题目】${stem}
【题型】${type}
【参考答案】${reference_answer}
【关键要点】${key_points}      # JSON 数组
【学员答案】${user_answer}
【本题章节】${chapter}

【输出要求】
- 用通俗中文讲解本题考点（≤ 200 字）
- 如果学员答错，明确指出哪个/哪些关键要点遗漏
- 如果学员答对，强化一下知识点的应用场景

【输出 JSON Schema】
{
  "title": "≤20 字标题",
  "explanation": "≤ 200 字讲解",
  "missed_points": ["学员遗漏的要点，答对时为 []"],
  "study_tip": "≤ 60 字学习建议"
}
```

#### 6.6.4 失败回退

| 异常 | 处理 |
|---|---|
| DeepSeek 超时（> 30s） | 流中断 → 客户端展示"讲解生成失败"按钮 + 显示参考答案 + 解析 |
| DeepSeek JSON 解析错 | 同上 |
| API key 失效 | 后端健康检查告警，前端展示"讲解服务暂不可用" |

**关键设计原则**：
- 学员考试 100% 完成无需讲解 → **零 LLM 调用**
- 讲解功能是"锦上添花"，**失败不影响考试**
- 学员可以选择不看讲解 → 系统一切正常

---

## 7. 数据模型

```sql
-- 核心 5 张表
CREATE TABLE subjects (
    id   TEXT PRIMARY KEY,           -- 'fin-mgmt'
    name TEXT NOT NULL               -- '财务管理'
);

CREATE TABLE chapters (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id TEXT NOT NULL REFERENCES subjects(id),
    code       TEXT NOT NULL,        -- 'ch1'
    title      TEXT NOT NULL,        -- '总论'
    weight     REAL NOT NULL DEFAULT 1.0,
    UNIQUE(subject_id, code)
);

CREATE TABLE questions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id    TEXT NOT NULL REFERENCES subjects(id),
    chapter_id    INTEGER NOT NULL REFERENCES chapters(id),
    type          TEXT NOT NULL CHECK(type IN ('single','multi','judge','calc','comprehensive')),
    difficulty    TEXT NOT NULL CHECK(difficulty IN ('easy','medium','hard')),
    stem          TEXT NOT NULL,
    options_json  TEXT,                -- JSON 数组，单选/多选时存；判断题 ['对','错']
    answer        TEXT NOT NULL,      -- 'A' / 'ABD' / '对' / 主观题答案文本
    key_points_json TEXT,             -- JSON 数组，主观题必填；客观题 NULL
    analysis      TEXT,                -- 解析
    source_pdf    TEXT NOT NULL,
    page_ref      INTEGER,
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_questions_chapter_type ON questions(chapter_id, type);

CREATE TABLE exam_attempts (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id            TEXT NOT NULL REFERENCES subjects(id),
    started_at            TEXT NOT NULL,
    submitted_at          TEXT,
    total_score           REAL,                       -- 0 ~ 100
    score_by_chapter_json TEXT,                        -- JSON {chapter_id: score}
    score_by_type_json    TEXT                         -- JSON {type: score}
);

CREATE TABLE attempt_answers (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    attempt_id           INTEGER NOT NULL REFERENCES exam_attempts(id),
    question_id          INTEGER NOT NULL REFERENCES questions(id),
    sequence             INTEGER NOT NULL,           -- 题目在试卷中的顺序
    user_answer          TEXT,                       -- 学员答案（统一字符串）
    is_correct           INTEGER,                    -- 0/1（客观题），主观题 NULL
    awarded_score        REAL NOT NULL,              -- 0 ~ full_score
    grading_comment      TEXT,                       -- 判分评语（关键词覆盖率结果）
    UNIQUE(attempt_id, question_id)
);
```

**不入库**：
- 概率分布走 `data/distributions/finance.json`（git 跟踪）
- 讲解内容**不入库**——每次按需生成，结果不持久化（YAGNI 不存"学习历史"）

---

## 8. Web API

| 方法 | 路径 | 用途 | 鉴权 | LLM? |
|---|---|---|---|---|
| POST | `/auth/login` | 单密码登录，颁发 JWT | 无 | 否 |
| GET  | `/dashboard` | 历史成绩 + 趋势 + 章节雷达数据 | 用户 JWT | 否 |
| POST | `/exams/start` | 启动一次模拟考，返回 `attempt_id` + 题目 | 用户 JWT | **否** |
| GET  | `/exams/{id}` | 拉取试卷（断线重连用） | 用户 JWT | 否 |
| POST | `/exams/{id}/submit` | 交卷，触发判分 pipeline | 用户 JWT | **否** |
| GET  | `/exams/{id}/result` | 成绩详情 + 每题评语 | 用户 JWT | 否 |
| POST | `/exams/{id}/explain` | 流式讲解某题（SSE） | 用户 JWT | **是**（按需） |
| GET  | `/admin/review/queue` | 开发期：可疑题列表 | 管理员 JWT（独立密码） | 否 |
| POST | `/admin/review/questions/{id}` | 人工修正 / 确认题目 | 管理员 JWT | 否 |

**关键设计**：
- REST + SSE（Server-Sent Events）for 讲解流式输出
- 客观题判分 + 关键词覆盖率判分 = **`submit` 内同步 < 1s**（纯算法）
- 讲解 = 异步流式，按需触发
- `/admin` 路由**单独鉴权**（独立 JWT + 独立密码），不与考试登录互通

---

## 9. 前端页面

```
/login              单密码登录
/                   首页: 最近成绩 + "开始模拟考" 按钮
/exam/:id/intro     考试介绍: 时长 / 题型分布 / 章节范围
/exam/:id/play      答题: 分题型分卡片, 单题标记, 顶部计时器
/exam/:id/result    成绩: 总分 + 章节雷达 + 各题评语 + "AI 讲解"按钮
/dashboard          趋势（折线）+ 章节雷达 + 历次成绩列表
/admin              开发期: 题目 review 界面（独立密码）
```

### 9.1 关键交互

- 答题页用**卡片切题**（不用 long scroll），5 种题型分组卡片
- 顶部计时器（120 分钟倒计时）后端 + 前端双显示
- 交卷前未答题**校验 + 二次确认弹窗**（避免误交）
- 章节雷达图：得分 / 满分 按章节聚合
- 趋势图：历次 `total_score` 折线 + 各章节得分叠加
- **AI 讲解按钮**：
  - 结果页每题下方有"AI 讲解"链接（默认折叠，按需点开）
  - 点击后流式接收讲解内容（SSE/Fetch ReadableStream）
  - 学员答对的题默认折叠，答错的题默认展开
  - 讲解区可点"再讲详细点"重新调用

### 9.2 视觉规范（占位）

- Element Plus 默认主题 + 中文（女友使用环境）
- 不强求设计感，**功能完整 + 易用** > 漂亮
- 后续若需要打磨，调 `/designer` 委派

---

## 10. 部署

### 10.1 docker-compose.yml（草案）

```yaml
services:
  app:
    build: .
    image: finance-exam-system:latest
    ports:
      - "8000:8000"            # 云服务商 nginx/caddy 反代 :443 → :8000
    volumes:
      - ./data:/app/data       # SQLite + 预处理产物
    env_file:
      - .env
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 5s
      retries: 3
```

### 10.2 .env.example（提交到 git）

```
DEEPSEEK_API_KEY=replace-me              # 仅 AI 讲解模块使用
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-v4-flash
USER_PASSWORD=replace-me
ADMIN_PASSWORD=replace-me
JWT_SECRET=replace-me-with-random-32-bytes
MIN_COVERAGE=0.6                          # 主观题关键词覆盖率门槛
```

### 10.3 最小云服务器配置

- 1 vCPU / 1GB RAM（出题+判分是纯算法，零 LLM；仅讲解偶发调用）
- 国内节点（女友访问延迟）
- 自动快照（SQLite 一份定时备份）

---

## 11. 测试策略

| 层 | 工具 | 范围 | 不测 |
|---|---|---|---|
| 解析器 | pytest | 用样本 PDF 验证解析正确率（≥ 95% 字段正确） | LLM 调用 |
| 抽题算法 | pytest | 给定分布+库，验证产出题目数/章节覆盖率/题型比例 | UI |
| 客观题判分 | pytest | 单选/多选/判断边界（漏选、多选、错选） | LLM |
| 主观题判分 | pytest | 关键词覆盖率各类边界（空答/短答/全覆盖/部分覆盖/< 门槛） | LLM |
| API 集成 | pytest + httpx | start → submit → result 全链路（**mock LLM**） | 真 LLM |
| 讲解流式 | pytest + httpx | mock DeepSeek 流式响应，验证 SSE chunk 处理 | 真 LLM |
| 前端组件 | Vitest | 答题卡片 / 计时器 / 雷达图 / AI 讲解按钮 | LLM |
| E2E | 手动 + 可选 Playwright | 完整模拟考一次（不查讲解） | 自动跑 |

**铁律**：**LLM 调用一律 mock**。CI / 本地测试**永不**调真实 DeepSeek。

---

## 12. DeepSeek API 集成（**仅讲解**）

### 12.1 模型选择

- **首选**：`deepseek-v4-flash`（用户指定）
- **回退**：`deepseek-chat`（V3 系列稳定版）—— 若 v4-flash 实际不存在时降级
- **实现前必做**：脚本启动时 ping 一次模型列表，确认可用；不可用则自动回退并日志告警

### 12.2 客户端封装（流式版）

```python
class DeepSeekExplainClient:
    """单例 FastAPI 依赖，**仅服务于 AI 讲解模块**，所有调用走流式"""
    def __init__(self, api_key, base_url, model):
        self.client = openai.OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    async def explain_stream(self, system: str, user: str):
        """流式输出讲解，SSE forward"""
        stream = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role":"system","content":system},
                      {"role":"user","content":user}],
            response_format={"type":"json_object"},
            timeout=30,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
```

### 12.3 成本与频率估算

| 操作 | 触发条件 | 调用次数 |
|---|---|---|
| 出题 | 每次模拟考 | 0 |
| 客观题判分 | 自动 | 0 |
| 主观题判分 | 自动 | 0 |
| AI 讲解 | 学员**主动点击**每题讲解按钮 | 0~41 / 模拟考 |

**关键点**：
- 学员正常考完试 = **0 次 LLM 调用**，零成本
- 仅在学员想知道某题"为什么"或"再讲详细点"时触发讲解
- 假设 1 次模拟考后学员点 5-10 次讲解 → 单次模拟考 LLM 成本 ¥0.005-0.01
- 10 元 API 余额 ≈ 1000-2000 次模拟考

---

## 13. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| PDF 解析偏移（题干被截断、答案配错选项） | 错题入库 → 女友考试错 | §5.2 机制 3 解析抽样比对 + 机制 4 Agent 团队多轮 + 人工 review |
| Agent 团队审查漏判（领域语义错） | 同上 | §5.2 机制 4 多轮迭代 + 人工全量 review 最后一道关 |
| 主观题判分过严/过松 | 分数偏离学员真实水平 | 关键词覆盖率门槛可调（`.env` MIN_COVERAGE）；先人工 review 几场结果再校准 |
| 学员答案全是"标准答案原文"复制（搜索式答题） | 关键词覆盖率虚高 | 后续可加"答案长度检查"+ 答案相似度检测（v2，不在 MVP） |
| AI 讲解超时 | 学员看不到讲解 | 流中断 → 显示"讲解生成失败" + 兜底显示参考答案 + 解析 |
| 客户/女友误改 admin 密码 | 锁死 | `.env` 文件 + git 历史告警（运维提示） |
| 云服务器宕机 | 不可用 | systemd restart + Docker healthcheck + 异地快照 |
| DeepSeek API 涨价 | 成本上升（但很小） | 模型可替换（接口兼容 OpenAI） |
| SQLite 损坏 | 题目库丢失 | 每次 commit 后快照 + 自动备份到 OSS |

---

## 14. 后续扩展点（不在本期范围）

### 14.1 新增学科

```bash
# 复用同一套 pipeline，只换资料目录
python -m preprocessor --subject accounting --input 会计资料/
python -m preprocessor --subject audit --input 审计资料/
```

新增条目：
- `subjects` 表加新行
- `data/parsed/<subject>.jsonl`
- `data/distributions/<subject>.json`
- 前端科目选择下拉（v2）

### 14.2 新增题型

类型新增 → Pydantic schema 加枚举 + 题型分数表 + 前端组件加渲染分支。
**不需改数据库 schema**（`type` 字段为枚举 TEXT，新增即加 enum 值）。

### 14.3 学员答案相似度检测（防搜索式答题）

v2 可加：用最长公共子串 / TF-IDF 检测学员答案与参考答案高度相似，警告提示（不影响 MVP 判分）。

### 14.4 数据指标（v2+）

未来可选：
- 错题统计（哪个章节错得最多）
- 答题时长分布
- **讲解调用次数 / 讲解反馈**（"讲解是否有帮助"）— 用作训练数据（**敏感**，需用户明确同意再做）

---

## 15. 项目目录结构

```
finance-exam-system/
├── docs/
│   └── superpowers/
│       ├── specs/
│       │   └── 2026-07-04-finance-exam-system-design.md   # 本文档
│       └── plans/
│           └── YYYY-MM-DD-finance-exam-system-plan.md     # 由 writing-plans 产出
├── data/
│   ├── raw/                      # PDF 提取中间产物，不入 git
│   ├── parsed/
│   │   ├── questions.jsonl       # 题目库（入 git，含 key_points）
│   │   └── knowledge_points.jsonl
│   ├── distributions/
│   │   └── finance.json          # 概率配置（入 git）
│   ├── qa/
│   │   ├── qa_report.md                # 规则校验（机制 1-3）报告
│   │   ├── review_iter_1.md            # Agent @oracle 架构 + @explorer 审查
│   │   ├── review_iter_2.md            # @oracle 财务领域审查
│   │   ├── review_iter_3.md            # @oracle 整体质量审查
│   │   └── review_iter_final.md        # 用户人工全量 review
│   └── final/
│       └── finance.db            # SQLite（不入 git，重生成）
├── packages/
│   ├── preprocessor/             # 离线预处理脚本（不调 LLM, 仅规则 + 统计）
│   │   ├── pdf_extract.py
│   │   ├── parse_questions.py
│   │   ├── validate_coverage.py
│   │   ├── build_distributions.py
│   │   └── tests/
│   ├── backend/                  # FastAPI 应用
│   │   ├── app/
│   │   │   ├── services/
│   │   │   │   ├── paper_assembler.py     # 出题算法（纯）
│   │   │   │   ├── grader.py             # 判分（纯规则）
│   │   │   │   ├── explain_service.py    # AI 讲解（流式）
│   │   │   │   └── deepseek_client.py    # DeepSeek 客户端（唯一用 LLM 入口）
│   │   │   ├── api/
│   │   │   ├── models/
│   │   │   └── main.py
│   │   ├── alembic/
│   │   └── tests/
│   └── frontend/                 # Vue 3 SPA
│       ├── src/
│       │   ├── components/
│       │   │   └── ExplainPanel.vue       # AI 讲解流式面板
│       │   ├── pages/
│       │   └── api/
│       └── tests/
├── deploy/
│   ├── docker-compose.yml
│   ├── Dockerfile
│   └── nginx.example.conf
├── .env.example
├── .gitignore
└── README.md
```

---

## 16. 实施里程碑

| # | 里程碑 | 产物 | 后续阶段 |
|---|---|---|---|
| M0 | spec 通过 review | 本文件 + git commit | → writing-plans |
| M1 | plan 通过 review | plans/*.md + git commit | → /comet-open |
| M2 | 资料预处理通过 | `data/parsed/questions.jsonl` + key_points 完整 + Agent 团队多轮审查通过 + 用户人工 review 100% | → 接入后端 |
| M3 | 后端 MVP 可用 | `POST /exams/start`（纯算法）+ `submit`（纯规则判分）全通 | → 前端 |
| M4 | 前端 MVP 可用 | `/play` + `/result` + 章节雷达通 | → AI 讲解 |
| M5 | AI 讲解开发完成 | `/explain` 流式端点 + Vue SSE 客户端 UI | → 自测 |
| M6 | 自测通过 + 部署 | 云服务器 `docker-compose up` 通 | → /comet-archive |

各阶段进入 comet 五阶段流水线（open → design → build → verify → archive）调度 Agent 团队。

---

## 附录 A — 资料目录映射

> 当前 `/home/ljh2923/opencode-project/EggEgg_Examination_System/财务管理资料/` 下的 12 个 PDF，按文件名做初步归类（实现阶段以解析器识别为准）：

| 文件名 | 类型 | 章节 |
|---|---|---|
| 第七章(1).pdf | 课件 | 第 7 章 |
| 第八章(1)(1).pdf | 课件 | 第 8 章 |
| 第九章(1).pdf | 课件 | 第 9 章 |
| 第一章课后练习(1)(1).pdf | 课后习题 | 第 1 章 |
| 第三章课后习题(1).pdf | 课后习题 | 第 3 章 |
| 第九章 即测即评(1)(1).pdf | 即测即评（自测） | 第 9 章 |
| 第二章课后习题(1)(1).pdf | 课后习题 | 第 2 章 |
| 第五章(1).pdf | 课件 | 第 5 章 |
| 第五章即测即评(1).pdf | 即测即评 | 第 5 章 |
| 第六章 即测即评(1).pdf | 即测即评 | 第 6 章 |
| 第六章(1).pdf | 课件 | 第 6 章 |
| 第四章(1).pdf | 课件 | 第 4 章 |

**当前资料缺口**：第 1、2、3 章缺课件 PDF；第 4-8 章缺课后习题。即测即评可作为优先题源。

**应对策略**：解析器不强求"每章都有三类 PDF"——按实际可解析的题目入库；缺题型由运行时抽样保证仍能从其他章节抽题。

---

## 附录 B — 题型统计与原题覆盖率（按章节初估）

| 章节 | 课件 | 课后习题 | 即测即评 | 预估题目 |
|---|---|---|---|---|
| 第 1 章 | ✗ | ✓ | ✗ | ≥ 30 |
| 第 2 章 | ✗ | ✓ | ✗ | ≥ 25 |
| 第 3 章 | ✗ | ✓ | ✗ | ≥ 20 |
| 第 4 章 | ✓ | ✗ | ✗ | ≥ 15（需从课件中提取题目形式内容）|
| 第 5 章 | ✓ | ✗ | ✓ | ≥ 25 |
| 第 6 章 | ✓ | ✗ | ✓ | ≥ 25 |
| 第 7 章 | ✓ | ✗ | ✗ | ≥ 15 |
| 第 8 章 | ✓ | ✗ | ✗ | ≥ 15 |
| 第 9 章 | ✓ | ✗ | ✓ | ≥ 25 |

**汇总**：9 章都有可用原题；总题库预估 ≥ 195 道。
**结论**：原题覆盖率充足，运行时**纯算法抽样**即可支撑每次模拟考纯算法生成，无需 LLM 改编。

> **重要修正（用户发现盲点）**：12 个 PDF 的**实质内容是题目+答案+解析组合**（即便标为"课件"的 PDF 也含大量习题），因此预处理阶段实际上是**结构化提取 + 质量校验 + key_points 标注**，而非"概念抽取 + 习题合成"。

---

## 附录 C — 关键决策日志

| # | 决策点 | 决议 | 来源 |
|---|---|---|---|
| 1 | 使用规模 | 1 人（女友） | 用户 |
| 2 | LLM 选型 | DeepSeek-v4-flash（仅**运行期讲解**用） | 用户 |
| 3 | 判分方式 | **客观自动 + 主观关键词覆盖率**（零 LLM） | 用户 |
| 4 | 模式 | 模拟考（唯一） | 用户 |
| 5 | 试卷结构 | 单 15×2 + 多 10×3 + 判 10×1 + 计 4×5 + 综 2×10 = 100 分 / 120 分钟 | 用户 |
| 6 | AI 反馈详细度 | **AI 讲解**（学员按需触发）+ 判分评语（关键词覆盖率结果） | 用户 |
| 7 | 历史保存 | 历次成绩 + 作答明细 + 趋势 + 雷达图 | 用户 |
| 8 | 预处理质量兜底 | **Agent 团队多轮迭代 + 用户人工全量 review**（**不调 DeepSeek**） | 用户 |
| 9 | DeepSeek 边界 | **仅运行期讲解**；开发期不参与预处理任何环节；出题 / 判分均不调 LLM | 用户 |
| 10 | 资料性质 | **题目+答案组合**（预处理任务 = 结构化提取 + 校验 + key_points 标注） | 用户 |
| 11 | 运行时出题 | **纯算法随机抽样**（章节×题型×难度加权），无 LLM 改编 | 用户 |
| 12 | 主观题判分依据 | **key_points 关键词覆盖率**（≥60% 起步按比例给分） | 用户 |
| 13 | AI 讲解触发 | 学员**主动点击**（失败时显示参考答案 + 解析兜底） | 用户 |

---

_文档结束。等待用户 review → 通过后调用 writing-plans skill → plan 落地 → 启动 /comet。_

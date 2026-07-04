# 财务管理考试系统 — 设计 Spec

| 字段 | 值 |
|---|---|
| **项目代号** | `finance-exam-system`（简称 FES） |
| **作者** | Agent 团队（由 orchestrator 调度） |
| **审核者** | 项目所有者（用户本人） |
| **创建日期** | 2026-07-04 |
| **状态** | 设计中（待 review） |
| **适用范围** | 当前 —— 财务管理科目；未来 —— 任何可解析 PDF 资料的科目 |

---

## 1. 概述

构建一个部署在远程云服务器的 Web 考试系统。当前学科为 **财务管理**，未来扩展其他学科。所有题目基于本地预处理资料（PDF），通过 **离线预处理 + 运行时 AI 改写** 两段式 pipeline 生成。**不引入 RAG**（用户在需求阶段明确决策）。

### 1.1 设计目标

1. **覆盖广**：题库覆盖财务管理的 9 章 × 5 种题型，每章节每题型有可复用原题 ≥ 3 道
2. **拿高分**：模拟考贴近中级会计 / 财经类考试标准结构（100 分 / 120 分钟 / 41 题），所有题目带章节归属、主观题带标准反馈（分数 + 评语 + 参考答案 + 章节）
3. **运行极速**：出题主要靠预处题库 + 算法抽样；LLM 仅在缺口时改编，**理想情况单次模拟考 ≤ 6 次 LLM 调用**（仅评判主观题）
4. **数据可信**：开发期由 **Agent 团队多轮迭代审查 + 用户人工全量 review** 把关入库；运行时 system prompt 显式 99% 信任预处理数据，杜绝 AI 重新质疑已校验内容（**DeepSeek 仅在运行期出题，不参与开发期数据审查**）
5. **单人单实例运维**：女友使用，无多租户、无复杂认证、无外部依赖（DB、Redis、Celery 都不引入），一份 `docker-compose up` 即可

### 1.2 明确非目标（YAGNI 清单）

- ❌ 多用户 / 多租户 / 公开注册
- ❌ 章节刷题模式 / 错题本 / 自由组卷
- ❌ 移动 App / PWA
- ❌ WebSocket / 实时协作
- ❌ K8s / 微服务拆分
- ❌ RAG / 向量数据库
- ❌ 多模型路由 / AB 测试

---

## 2. 用户画像与场景

| 项 | 值 |
|---|---|
| **真实用户** | 1 人（女友） |
| **使用设备** | 任意带浏览器的设备 |
| **使用频次** | 备考期间 1-3 次/周 |
| **网络** | 国内（云服务器国内节点） |
| **关键体验目标** | 「点击开始 → ≤ 3 秒看到试卷」+「主观题评语让她知道哪里学得不好」 |
| **失败容忍** | 错题 / 解析缺失 = 学习失败，宁可拒绝 AI 编造也要保证质量 |

**运维边界**：女友无需任何运维能力；只有项目所有者（用户）会做配置（DeepSeek API key、管理员密码）。

---

## 3. 技术栈

| 层 | 选型 | 关键理由 |
|---|---|---|
| 后端语言 | Python 3.11+ | LLM SDK / PDF 解析生态成熟；async 对 LLM 长调用友好 |
| Web 框架 | FastAPI + Uvicorn | 类型安全；OpenAPI 自动生成；异步支持 |
| ORM | SQLAlchemy 2.0 + Alembic | 迁移可控；类型化查询 |
| 数据库 | SQLite（WAL 模式） | 零运维；单文件易备份；JSON 字段支持好 |
| LLM SDK | `openai` Python 库（DeepSeek 兼容 OpenAI API） | 一行 `base_url` 切换 |
| PDF 解析 | `pdfplumber` | 中文文本 + 坐标识别 |
| Schema 校验 | Pydantic 2 | 严格模式 |
| 前端 | Vue 3 + Vite + TypeScript | 上手快；组件库成熟 |
| UI 组件 | Element Plus | 中文文档齐；表格 / 表单齐全 |
| 图表 | ECharts | 趋势图 + 雷达图 |
| 测试（后端） | pytest + httpx | 异步测试方便 |
| 测试（前端） | Vitest + Vue Test Utils | 标准搭配 |
| 部署 | Docker Compose 单容器 | 一份配置、一键启动 |

---

## 4. 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│  浏览器 (Vue 3 SPA + Element Plus + ECharts)                │
└──────────────────────────────┬──────────────────────────────┘
                               │ HTTPS（云服务商反代）
                               ▼
┌─────────────────────────────────────────────────────────────┐
│  FastAPI 单进程 (Uvicorn)                                    │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐     │
│  │ 试卷生成 │  │ 答题批改 │  │ 成绩历史 │  │ 管理路由 │     │
│  │ Service  │  │ Service  │  │ Service  │  │  /admin  │     │
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
                │  PDF → 文本 → 题目库 → 校验  │
                └──────────────────────────────┘
                                ▲
                                │ Agent 团队多视角审查
                ┌──────────────────────────────────┐
                │  @oracle (架构 + 财务逻辑)        │
                │  @explorer (数据完整性扫描)      │
                │  用户 (全量人工 review)           │
                └──────────────────────────────────┘
```

### 4.1 部署拓扑

```
                        互联网
                          │ HTTPS
                          ▼
              ┌─────────────────────┐
              │ 云服务商反代          │
              │ (nginx / caddy)      │
              │ 自动签发 TLS          │
              └──────────┬──────────┘
                         │ :443 → :8000
                         ▼
              ┌─────────────────────┐
              │ Docker 容器          │
              │ FastAPI + 静态前端   │
              │ + SQLite            │
              │ + 预处 jsonl/json   │
              └─────────────────────┘
                   ↑         ↑
                   │         └── .env (DeepSeek key, 管理密码)
                   │
              ./data 挂载 (SQLite + 中间产物)
```

---

## 5. 资料预处理 Pipeline（开发期一次性）

> 用户在对话中明确：**开发期由 Agent 团队多轮迭代审查 + 用户人工全量 review，整库过关后入库。运行时仅消费库 + 必要时改写。DeepSeek 不参与开发期数据审查。**

### 5.1 Pipeline 总览

```
┌─────────────────────────────────────────────────────────────┐
│ ① PDF → 纯文本 + 坐标 (pdfplumber)                         │
│    产出: data/raw/*.txt + layout.json                       │
│    门:   文本长度校验 / 章节标题检出率                       │
└──────────────────────────────┬──────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ ② 纯文本 → 题目对象 (Python 解析器)                         │
│    产出: data/parsed/questions.jsonl                         │
│    字段: id / type / chapter / number / stem / options /    │
│          answer / analysis / difficulty / source_pdf /       │
│          page_ref                                            │
│    门:   Pydantic 严格校验                                   │
│          (答案格式 / 选项个数 / 章节白名单)                   │
└──────────────────────────────┬──────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ ③ 知识点 + 概率分布 (离线计算)                              │
│    产出: data/distributions/finance.json                    │
│          data/parsed/knowledge_points.jsonl                  │
│    内容: 章节×题型 抽样权重 + 章节知识图谱                    │
└──────────────────────────────┬──────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ ④ 强质量校验 (纯规则 + 统计 + 抽样 — **不调 LLM**)          │
│    a) 覆盖率自检: 9 章 × 5 题型 ≥ N 题                       │
│    b) 答案合理性: 选项唯一、答案∈选项集合                     │
│    c) 章节归属: 每章覆盖率、孤立题检测                        │
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
│      → schema / 覆盖率 / 抽样比对 通过？                       │
│                                                             │
│    迭代 2 — 领域逻辑审查：                                    │
│      @oracle (财务领域) 抽 30-50 道题                            │
│      → 答案合理性 / 解析完整性 / 章节归属 无误？                  │
│                                                             │
│    迭代 3 — 跨章节一致性 + 解析深度：                          │
│      @oracle (题库整体质量)                                   │
│      → 是否存在模糊答案、解析空题、孤立题？                       │
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
| 2 | **覆盖率自检** | 9 章 × 5 题型 ≥ N 题 | 章节漏题 |
| 3 | **解析抽样比对** | 50 道随机题 vs 原 PDF 反向比对 | 解析偏移 |
| 4 | **Agent 团队多轮迭代审查 + 用户人工全量 review** | @oracle (架构+领域双轮) + @explorer (数据完整性) + 用户 `/admin` 全量过目 | 答案错 / 解析缺 / 章节误标 / 任何自动校验漏判 |
| + | **版本化 + 可重跑** | git 管理 + 幂等脚本 | 不可逆改动 |

**4 大机制的设计哲学**：
- 规则校验（机制 1-3）：抓**结构化**问题 — 字段缺失、类型错、缺题型
- Agent + 人工（机制 4）：抓**领域**问题 — 答案语义错、解析深度、领域逻辑
- 两者互补，缺一不可

### 5.3 概率分布算法

```json
{
  "subject": "财务管理",
  "chapters": [
    { "id": "ch1", "title": "总论",       "weight": 1.0, "questions_count": 45 },
    { "id": "ch2", "title": "财务活动",   "weight": 1.0, "questions_count": 32 },
    { "id": "ch3", "title": "财务估价",   "weight": 1.2, "questions_count": 28 },
    { "id": "ch4", "title": "投资管理",   "weight": 1.5, "questions_count": 51 },
    { "id": "ch5", "title": "筹资管理",   "weight": 1.5, "questions_count": 47 },
    { "id": "ch6", "title": "营运资金",   "weight": 1.0, "questions_count": 38 },
    { "id": "ch7", "title": "收益分配",   "weight": 1.0, "questions_count": 25 },
    { "id": "ch8", "title": "财务预算",   "weight": 0.8, "questions_count": 18 },
    { "id": "ch9", "title": "财务分析",   "weight": 1.0, "questions_count": 30 }
  ],
  "auto_derivation": "P(chapter, type) ∝ questions_count × weight",
  "manual_override_allowed": true,
  "constraint": "P(chapter, type) > 0 仅当该章该题型可用原题 ≥ 3；否则强制 LLM 改编"
}
```

---

## 6. 运行时出题 + 判分 Pipeline

### 6.1 时序图

```
  浏览器                              FastAPI                              DeepSeek
   │                                    │                                    │
   │  POST /exams/start                 │                                    │
   │ ──────────────────────────────────►│                                    │
   │                                    │  ① 查 distributions.json            │
   │                                    │  ② 抽题算法（纯内存）               │
   │                                    │  ③ 按权重从预处库查候选              │
   │                                    │  ④ 缺口 → DeepSeek 改编              │
   │                                    │ ──────────────────────────────────►│
   │                                    │ ◄──── 改编题 JSON ─────────────────│
   │                                    │  ⑤ 全部入库 attempt_questions        │
   │ ◄─── 试卷 (id, questions[]) ──────│                                    │
   │                                    │                                    │
   │  ... 答题中（计时器服务端同步）...    │                                    │
   │                                    │                                    │
   │  POST /exams/{id}/submit           │                                    │
   │ ──────────────────────────────────►│                                    │
   │                                    │  ⑥ 客观题自动判分                   │
   │                                    │  ⑦ 主观题 (6 道) → DeepSeek 评判    │
   │                                    │ ──────────────────────────────────►│
   │                                    │ ◄──── 评判结果 JSON ──────────────│
   │                                    │  ⑧ 写入成绩 + 作答明细              │
   │ ◄─── 成绩 + 章节分 + 评语 ─────────│                                    │
   │                                    │                                    │
```

### 6.2 抽题算法（核心，≤ 80 行 Python 风格描述）

```python
def assemble_paper(subject: str, paper_spec: PaperSpec) -> Paper:
    """主入口：按试卷规格从预处库 + 算法 + LLM 补位 组卷"""
    dist = load_distributions(subject)
    bank = load_question_bank(subject)

    slots = build_slots(paper_spec)             # 41 个空槽位
    out: list[Question] = []

    for slot in slots:
        chapter = sample_chapter(dist, slot.type)   # 按权重加权采样
        candidates = bank.query(
            type=slot.type,
            chapter=chapter,
            difficulty=slot.difficulty_hint,
            limit=10
        )
        if candidates:
            out.append(candidates.random_one())      # 直接复用原题
        else:
            # 缺口：在预处库中找最近 3 道同章节同题型作 few-shot
            seeds = bank.query(type=slot.type, chapter=nearest_chapter(dist, chapter), limit=3)
            prompt = build_adapt_prompt(slot, seeds)  # system prompt 见 6.4
            out.append(call_deepseek(prompt))         # LLM 改编

    return Paper(questions=out, spec=paper_spec)
```

**关键不变量**：
- 算法主体 = 查表 + 加权采样，**不调 LLM**
- 章节该题型 ≥ 3 题保护性约束（防止 LLM 集中在某章瞎编）
- LLM 改编触发后仍满足题型 / 章节要求（re-validate）

### 6.3 章节权重采样

```python
def sample_chapter(dist, q_type):
    """按 dist.chapters × weight 加权采样"""
    eligible = [
        (ch, ch.weight)
        for ch in dist.chapters
        if dist.has_enough(ch.id, q_type, min_n=3)   # 保护性约束
    ]
    if not eligible:
        return dist.fallback_chapter                  # 兜底
    return weighted_choice(eligible)
```

### 6.4 System Prompt 模板（运行时，关键 3 套）

#### 6.4.1 出题 Prompt（极少触发，仅当缺口时）

```
【数据可信度约定】
本场出题基于已经 Agent 团队多轮迭代审查 + 用户人工全量 review 的预处理数据。
请 99% 信任这些数据，不要重新质疑其准确性。
仅当拼装数据自相矛盾（如同章节多套冲突答案）时才 stop 并报告异常。
否则直接基于章节要点、题型、概率分布，创作题目。

【任务】
基于【章节要点：${chapter}】+【题型：${type}】+【难度：${difficulty}】，
生成 1 道符合【输出 JSON Schema】的题目。

【输出 JSON Schema】
{ "stem": "...", "options": ["A...","B...","C...","D..."], "answer": "A",
  "analysis": "≤120 字解析", "chapter": "${chapter}" }
```

#### 6.4.2 改编 Prompt（缺口补位）

```
[同 6.4.1 数据可信度约定]

【任务】
参考下列原题，做数值改编 / 场景重写，**保持答案正确性 + 难度等价**。
- 原题 #${seed_id}：...
- 原题 #${seed_id}：...
- 原题 #${seed_id}：...

要求：题型不变、章节不变、答案一致。
```

#### 6.4.3 评判 Prompt

```
你是财务管理助教。请依据【参考答案】+【评分要点】对【学员答案】打分。

【题目】${stem}
【参考答案】${reference_answer}
【评分要点】${key_points}    # ≤5 条要点数组
【学员答案】${user_answer}

【输出 JSON Schema】
{
  "score": 0,             # 0 ~ ${max_score}
  "comment": "≤80 字评语",
  "reference_answer": "${reference_answer}",
  "chapter": "${chapter}"
}
```

### 6.5 评判阈值

| 条件 | 处理 |
|---|---|
| 学员答案空白 | `score = 0, comment = "未作答"` |
| 答案字数 < 5 字 | `score = 0, comment = "答案过短，无法评估"` |
| 正常情况 | 让 DeepSeek 自由评判 |
| DeepSeek 返回 score 越界 | 钳制到 `[0, max_score]`；comment 为空则补"无评语" |

### 6.6 异常处理

| 异常 | 恢复策略 |
|---|---|
| DeepSeek 超时（单题 > 30s） | 自动重试 1 次，仍失败则该题标"出题失败"，通知用户重新开始 |
| 返回 JSON 格式错 | 重新调用 + 严格提示；仍失败则用预处库同章节同题型替换 |
| 章节该题型候选数 < 3 | 跳过该章节；改用全章节兜底池 |
| 学员断线 / 重复提交 | 按 `attempt_id` 幂等：第一次有效，后续忽略 |

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
    options_json  TEXT,                -- JSON 数组，单选/多选时存
    answer        TEXT NOT NULL,      -- "A" / "ABD" / "对" / 主观题答案文本
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
    user_answer_json     TEXT,                       -- 学员答案
    is_correct           INTEGER,                    -- 0/1（客观题），主观题 NULL
    awarded_score        REAL NOT NULL,              -- 客观题: 0/满分; 主观题: LLM 评判
    llm_comment          TEXT,                       -- 主观题评语
    llm_reference_answer TEXT,                       -- 主观题标准答案
    UNIQUE(attempt_id, question_id)
);
```

**不入库**：概率分布走 `data/distributions/finance.json`（git 跟踪）；运行时不依赖数据库 schema 改它。

---

## 8. Web API

| 方法 | 路径 | 用途 | 鉴权 |
|---|---|---|---|
| POST | `/auth/login` | 单密码登录，颁发 JWT | 无 |
| GET  | `/dashboard` | 历史成绩 + 趋势 + 章节雷达数据 | 用户 JWT |
| POST | `/exams/start` | 启动一次模拟考，返回 `attempt_id` + 题目 | 用户 JWT |
| GET  | `/exams/{id}` | 拉取试卷（断线重连用） | 用户 JWT |
| POST | `/exams/{id}/submit` | 交卷，触发判分 pipeline | 用户 JWT |
| GET  | `/exams/{id}/result` | 成绩详情 + 每题评语 | 用户 JWT |
| GET  | `/admin/review/queue` | 开发期：可疑题列表 | 管理员 JWT（独立密码） |
| POST | `/admin/review/questions/{id}` | 人工修正 / 确认题目 | 管理员 JWT |

**关键设计**：
- REST 风格，无 WebSocket
- 主观题评判 = `submit` 内同步等待 ≤ 60s（6 道 × 8s 估算）
- `/admin` 路由**单独鉴权**（独立 JWT + 独立密码），不与考试登录互通
- 所有错误返回 `{ error_code, message }` JSON，主键冲突 / 鉴权失败标准 HTTP 状态码

---

## 9. 前端页面

```
/login              单密码登录
/                   首页: 最近成绩 + "开始模拟考" 按钮
/exam/:id/intro     考试介绍: 时长 / 题型分布 / 章节范围
/exam/:id/play      答题: 分题型分卡片, 单题标记, 顶部计时器
/exam/:id/result    成绩: 总分 + 章节雷达 + 各题评语
/dashboard          趋势（折线）+ 章节雷达 + 历次成绩列表
/admin              开发期: 题目 review 界面（独立密码）
```

### 9.1 关键交互

- 答题页用**卡片切题**（不用 long scroll），5 种题型分组卡片
- 顶部计时器（120 分钟倒计时）后端 + 前端双显示
- 交卷前未答题**校验 + 二次确认弹窗**（避免误交）
- 章节雷达图：得分 / 满分 按章节聚合
- 趋势图：历次 `total_score` 折线 + 各章节得分叠加

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
DEEPSEEK_API_KEY=replace-me
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-v4-flash
USER_PASSWORD=replace-me
ADMIN_PASSWORD=replace-me
JWT_SECRET=replace-me-with-random-32-bytes
```

### 10.3 最小云服务器配置

- 1 vCPU / 1GB RAM（出题/评判是 LLM 端做，本地只调度）
- 国内节点（女友访问延迟）
- 自动快照（SQLite 一份定时备份）

---

## 11. 测试策略

| 层 | 工具 | 范围 | 不测 |
|---|---|---|---|
| 解析器 | pytest | 用样本 PDF 验证解析正确率（≥ 95% 字段正确） | LLM 调用 |
| 抽题算法 | pytest | 给定分布+库，验证产出题目数/章节覆盖率/题型比例 | UI |
| 评判阈值 | pytest | 空白 / 短答 / 正常 三类边界 | LLM |
| API 集成 | pytest + httpx | start → submit → result 全链路（**mock LLM**） | 真 LLM |
| 前端组件 | Vitest | 答题卡片 / 计时器 / 雷达图 | LLM |
| E2E | 手动 + 可选 Playwright | 完整模拟考一次 | 自动跑 |

**铁律**：**LLM 调用一律 mock**。CI / 本地测试**永不**调真实 DeepSeek。

---

## 12. DeepSeek API 集成

### 12.1 模型选择

- **首选**：`deepseek-v4-flash`（用户指定）
- **回退**：`deepseek-chat`（V3 系列稳定版）—— 若 v4-flash 实际不存在时降级
- **实现前必做**：脚本启动时 ping 一次模型列表，确认可用；不可用则自动回退并日志告警

### 12.2 客户端封装

```python
class DeepSeekClient:
    """单例 FastAPI 依赖，所有 DeepSeek 调用统一入口"""
    def __init__(self, api_key, base_url, model):
        self.client = openai.OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    def chat_json(self, system: str, user: str, *, max_tokens=2000, timeout=30) -> dict:
        """通用 JSON 模式调用 + 自动重试 + format 校验"""
        for attempt in range(2):  # 最多 1 次重试
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role":"system","content":system},
                              {"role":"user","content":user}],
                    response_format={"type":"json_object"},
                    timeout=timeout,
                )
                return json.loads(resp.choices[0].message.content)
            except (JSONDecodeError, APITimeoutError) as e:
                log.warning(f"retry {attempt}: {e}")
        raise DeepSeekExhausted("...")
```

### 12.3 速率与成本估算

| 操作 | 单次调用 token | 每次模拟考调用次数 | 估算成本（¥） |
|---|---|---|---|
| 出题（缺口改编） | ~800 | 0-5 | 0.001-0.005 |
| 评判主观题 | ~600 | 6 | 0.003 |
| **单次模拟考合计** | — | **6-11** | **~0.004-0.008** |

10 元 API 余额可覆盖 **上千次模拟考**，无需预算优化。

---

## 13. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| PDF 解析偏移（题干被截断、答案配错选项） | 错题入库 → 女友考试错 | §5.2 机制 3 解析抽样比对 + 机制 4 Agent 团队多轮 + 人工 review |
| Agent 团队审查漏判（领域语义错） | 同上 | §5.2 机制 4 多轮迭代 + 人工全量 review 最后一道关；§6.4.1 99% 信任但矛盾 flag |
| 运行时 DeepSeek 改编生成离谱题 | 试卷异常 | §6.6 重试 + 同章节同题型替换 |
| 客户/女友误改 admin 密码 | 锁死 | `.env` 文件 + git 历史告警（运维提示） |
| 云服务器宕机 | 不可用 | systemd restart + Docker healthcheck + 异地快照 |
| DeepSeek API 涨价 | 成本上升 | 模型可替换（接口兼容 OpenAI） |
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

类型新增 → Pydantic schema 加枚举 + 出题 prompt 加分支 + 题型分数表。
不需改数据库 schema（`type` 字段），只需前端组件加渲染分支。

### 14.3 数据指标

未来可选：
- 错题统计（哪个章节错得最多）
- 答题时长分布
- 主观题评语留存供后续 review 用作训练数据（**敏感**，需用户明确同意后再做）

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
│   │   ├── questions.jsonl       # 题目库（入 git）
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
│   │   ├── alembic/
│   │   └── tests/
│   └── frontend/                 # Vue 3 SPA
│       ├── src/
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
| M2 | 资料预处理通过 | `data/parsed/questions.jsonl` + Agent 团队多轮迭代审查通过 + 用户人工 review 100% | → 接入后端 |
| M3 | 后端 MVP 可用 | `POST /exams/start` + `submit` 全通 | → 前端 |
| M4 | 前端 MVP 可用 | `/play` + `/result` 通 | → 自测 |
| M5 | 自测通过 + 部署 | 云服务器 `docker-compose up` 通 | → /comet-archive |

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

**应对策略**：解析器不强求"每章都有三类 PDF"——按实际可解析的题目入库，缺口由 LLM 改编补位（运行期）。

---

## 附录 B — 题型统计与原题覆盖率（按章节初估）

| 章节 | 课件 | 课后习题 | 即测即评 | 预估题目 |
|---|---|---|---|---|
| 第 1 章 | ✗ | ✓ | ✗ | ≥ 30 |
| 第 2 章 | ✗ | ✓ | ✗ | ≥ 25 |
| 第 3 章 | ✗ | ✓ | ✗ | ≥ 20 |
| 第 4 章 | ✓ | ✗ | ✗ | 0（仅课件，需 AI 改编） |
| 第 5 章 | ✓ | ✗ | ✓ | ≥ 25 |
| 第 6 章 | ✓ | ✗ | ✓ | ≥ 25 |
| 第 7 章 | ✓ | ✗ | ✗ | 0（仅课件，需 AI 改编） |
| 第 8 章 | ✓ | ✗ | ✗ | 0（仅课件，需 AI 改编） |
| 第 9 章 | ✓ | ✗ | ✓ | ≥ 25 |

**汇总**：6 章有原题，3 章（第 4、7、8 章）只有课件，需依赖 AI 改编。
**结论**：覆盖率约 70%，剩余 30% 由 LLM 改编 + system prompt 99% 信任保障。

> **重要修正（用户发现盲点）**：12 个 PDF 的**实质内容是题目+答案+解析组合**（即便标为"课件"的 PDF 也含大量习题），因此预处理阶段实际上是**结构化提取 + 质量校验**，而非"概念抽取 + 习题合成"。

---

## 附录 C — 关键决策日志

| # | 决策点 | 决议 | 来源 |
|---|---|---|---|
| 1 | 使用规模 | 1 人（女友） | 用户 |
| 2 | LLM 选型 | DeepSeek-v4-flash（仅**运行期**） | 用户 |
| 3 | 判分方式 | 客观自动 + 主观 AI 评判 | 用户 |
| 4 | 模式 | 模拟考（唯一） | 用户 |
| 5 | 试卷结构 | 单 15×2 + 多 10×3 + 判 10×1 + 计 4×5 + 综 2×10 = 100 分 / 120 分钟 | 用户 |
| 6 | AI 反馈详细度 | 标准（分数 + 评语 + 参考答案 + 章节） | 用户 |
| 7 | 历史保存 | 历次成绩 + 作答明细 + 趋势 + 雷达图 | 用户 |
| 8 | 预处理质量兜底 | **Agent 团队多轮迭代 + 用户人工全量 review**（**不调 DeepSeek**） | 用户 |
| 9 | DeepSeek 边界 | **仅运行期出题 + 评判**；开发期不参与预处理任何环节 | 用户 |
| 10 | 运行时 system prompt | **99% 信任预处理数据**，仅在拼装数据自相矛盾时 flag | 用户 |
| 11 | 资料性质 | **题目+答案组合**（预处理任务 = 结构化提取 + 校验，非概念抽取） | 用户 |

---

_文档结束。等待用户 review → 通过后调用 writing-plans skill → plan 落地 → 启动 /comet。_

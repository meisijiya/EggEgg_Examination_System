# 财务管理考试系统 — 设计 Spec

| 字段 | 值 |
|---|---|
| **项目代号** | `finance-exam-system`（简称 FES） |
| **作者** | Agent 团队（由 orchestrator 调度） |
| **审核者** | 项目所有者（用户本人） |
| **创建日期** | 2026-07-04 |
| **版本** | v8 |
| **状态** | 实现完成收尾（待 archive） |
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
7. **AI 讲解单独 LLM role**：运行时 LLM 严格划分为两个独立 role — **AI 讲解**（学员按需触发）和 **mixed mode 改编**（防幻觉护栏），两者都走显式 prompt + 失败 fallback；**出题 standard mode 和判分任何环节永不允许 LLM**

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
│          answer / key_points[] / analysis /                  │
│          / source_pdf / page_ref                             │
│          difficulty=null（阶段 ②.5 填充）                    │
│    门:   Pydantic 严格校验（字段全、类型对、答案∈选项集）   │
│    ★ 本阶段对"计算分析题 / 综合题" 标记 key_points[]，       │
│      作为运行时关键词覆盖率判分的依据                         │
└──────────────────────────────┬──────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ ②.5 DeepSeek 难度评估（开发期一次性）                  │
│    唯一允许开发期调 LLM 的环节                              │
│                                                             │
│    角色 prompt:                                              │
│      "你是财务管理科目的本科学生，正在备考期末考试。         │
│       学习画像：平时听课一般（基本概念都懂，深度讲解跟不上） │
│              作业有做（基础题能对，应用题吃力）             │
│              最近在努力复习（在补薄弱章节，难题仍在攻克）   │
│       ...（详见 §12.2 润色版）"                              │
│                                                             │
│    输入: data/parsed/questions.jsonl（仅取 id, type, stem, │
│          options, answer, analysis）                          │
│    输出: 每题 difficulty ∈ {1,2,3} + reasoning              │
│    速度: 批量异步 ~ 2s/题 × 200+ 题 ≈ 7 分钟                       │
│    校核: @oracle 抽样 5%（≥10 道）人工复核                   │
│                                                             │
│    边界澄清:                                                 │
│      ✓ 允许：评估难度、识别薄弱知识点                         │
│      ✗ 禁止：质疑答案正误（答案由机制 4 人工审查兜底）       │
│      ✗ 禁止：判断解析是否完整（解析完整性由机制 3 抽样比对） │
└──────────────────────────────┬──────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ ③ 章节×题型概率分布表 (离线计算)                            │
│    产出: data/distributions/finance.json                    │
│    内容: 章节权重、题型目标数、难度目标比例、保护性约束      │
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

> **关键边界**：
> - **DeepSeek 不参与答案校验 / 解析完整性判断**——开发期只能由 Agent 团队（多轮迭代）+ 用户人工 review 兜底。LLM 评判对"它训练数据里也有的内容"未必能可靠识别错误，硬塞会引入假阳性。
> - **DeepSeek 仅在阶段 ②.5 评估难度**——这是开发期**唯一允许**调用 LLM 的环节，且只标 1-2-3 不涉及答案对错。

| # | 机制 | 实现 | 防 |
|---|---|---|---|
| 1 | **Schema 硬校验** | Pydantic 2 严格模式 | 解析器默默吞错 |
| 2 | **覆盖率自检** | 9 章 × 5 题型 ≥ 3 题；key_points ≥ 3（计算/综合） | 章节漏题；判分失效 |
| 3 | **解析抽样比对** | 50 道随机题 vs 原 PDF 反向比对 | 解析偏移 |
| 4 | **Agent 团队多轮迭代审查 + 用户人工全量 review** | @oracle (架构+领域双轮) + @explorer (数据完整性) + 用户 `/admin` 全量过目 | 答案错 / 解析缺 / 章节误标 / key_points 表达模糊 |
| ＋ | **DeepSeek 难度标注抽样复核** | @oracle 抽 5%（≥10 道）核对 1/2/3 标签是否合理 | 难度评估误差（不影响判分） |

**质量机制落地的代码职责边界**（v8 增量规范）：
- 阶段 ② PDF→jsonl / ③ 分布表 / ④ 强质量校验：**全部不调 LLM**，由 `packages/preprocessor/` 中的纯 Python 脚本完成
- 阶段 ②.5 难度评估：单次 LLM 批跑 + dump 到 `data/parsed/difficulty/ch{1..9}.jsonl`（v8 9 个章节 jsonl 已落地,见 §15 目录树）
- 阶段 ⑤ 三轮迭代 + 用户人工：`/admin` 路由 + `data/qa/review_iter_*.md` 文档沉淀

### 5.3 运行时抽样算法（**章节 × 题型 × 难度** 三维加权随机）

> 这是开发期交付给运行时的核心算法：保证每次模拟考的章节覆盖完整、题型比例正确、**难度分布贴近真实考试**（30% 简单 + 50% 中等 + 20% 困难）。运行时由 `PaperAssembler` 类全权负责（`packages/backend/app/services/paper_assembler.py`）。

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

    # 难度分布（贴近中级会计真实考试）
    difficulty_target = {
        "easy": 0.30,     # 30% 基础题
        "medium": 0.50,   # 50% 中等题
        "hard": 0.20,     # 20% 难题
    }
    difficulty_prob = DifficultyProb(difficulty_target)

    return Distribution(chapters, chapter_prob, type_prob, difficulty_prob)
```

**算法约束**：
- 一章被选中的概率 ∝ 该章可用原题数 × 用户权重
- 一题型被选中的概率 = 试卷规格中的题型占比
- 一难度被选中的概率 = `difficulty_target`（可调）
- 章节 × 题型组合时，**优先选取题量 ≥ 3 的难度组**（保护性约束）
- 抽样保证**至少覆盖 9 章中的 8 章**（填空平衡机制）
- 难度组不足时降级到"题型 + 章节"组合，从相邻难度补足
- 单次模拟考分布 = `weighted_sample(population=chapters, weights=chapter_prob, k=41)` + 难度维度联合约束

**运行时三段式加权**：
1. **章节权重抽样**：候选章节权重 ∝ 该章节该题型可用题数（保护性约束 ≥ 3）
2. **难度预分配**：按 `difficulty_target` 比例对 41 个槽位预分配难度标签，整段 shuffle
3. **(chapter, type, difficulty) 三元组抽题**：三轮缺口 fallback（跨章节 → 跨难度 → 跨题型 → 全池兜底）

**8 章覆盖保证（v6 起落地）**：
- `_ensure_chapter_coverage` 在最后一次扫时**主动替换**未覆盖章节（题型不变），不依赖纯概率
- 9 章题库在 41 题试卷下，最坏情况也可能漏 1 章；这是显式可接受的 v1 权衡（v2 可补 adaptive sampling）

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

### 6.2 出题算法（核心，零 LLM；mixed mode 接入 AI 改编）

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

#### 6.2.1 统一出题入口（v8 新增 mixed mode）

```python
async def assemble_paper_async(
    subject: str,
    paper_spec: PaperSpec,
    mode: str = "standard",       # 'standard' | 'mixed'
    deepseek_client: DeepSeekClient | None = None,
) -> list[dict]:
    """统一出题入口 — 按 mode 路由

    - mode='standard' → 走原 assemble() 路径,零 LLM
    - mode='mixed'    → 跑标准抽样 + 选 ~30% 槽位做 AI 改编(
                          adapt_service;并发 by asyncio.gather +
                          Semaphore(12);耗时以 §11 v8 性能基线表为准,
                          worst case < 300s)
    """
```

**mixed mode 行为**（fix-20 落地，启动耗时三档以 §11 v8 性能基线表为权威值）：
1. 调 `PaperAssembler.assemble()` 拿到标准试卷（41 题，无 LLM）
2. 随机选 ~30% 题（`max(1, int(41 * 0.30))` ≈ 13 题）
3. 对每题用 `asyncio.gather` + `Semaphore(12)` 并发调 `adapt_one_question()` —— **LLM 改编单题 ≈ 5-15s, 13 题并发 ≈ 90s p50**
4. 改编护栏三重校验（type 不变 / key_points 复用 / 答案等价）—— 任一失败 → fallback 保留原题
5. 返回带 `is_adapted / source_question_id / adapted_answer / adapted_key_points / adapted_analysis` 标注的试卷

**混模式改为并发后**（fix-25 P0 优化，启动耗时三档以 §11 权威表为准）：
- 13 题串行 = 144-200s
- **13 题 asyncio.gather(Semaphore=12) = p50 ~90s / p95 < 180s / worst case < 300s**
- 前端 axios timeout 同步调到 180s 覆盖 worst case（fix-19）

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

```python
def grade_answer(
    q_type: str,
    correct_answer: str,         # 客观题 or 主观题参考答案
    user_answer: str,
    full_score: float,
    key_points: list[str] | None = None,    # 主观题必填 ≥ 3
    min_coverage: float = 0.6,
) -> GradedAnswer:
    """v8 统一判分入口 — 接受 correct_answer 参数注入(支持 adapted_answer)"""
    ...
```

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

#### 6.4.2 主观题（计算分析 / 综合题,关键词覆盖率）

```python
STOP_WORDS = {"的", "了", "是", "在", "和", "与", "或", "等", ...}  # 中文停用词表

def parse_sub_answers(user_answer: str) -> list[str]:
    """三档拆分小问答案(按 v5 实现,fix-17 强化):

    1. 编号格式:"(1)xxx (2)yyy" / "1.xxx 2.yyy"
    2. 分号分隔:"xxx;yyy;zzz"
    3. 整段(无法识别编号/分号):返回 [user_answer]

    防小数误识别:regex `(?!\d)` 确保编号后跟非数字字符
    (e.g. "答案 1.5" 不被切成 ["答案 1", "5"])
    """

def grade_subjective(question, user_answer, key_points: list[str]) -> GradedAnswer:
    """覆盖率达到 100% 给满分;60%~100% 按比例;< 60% 给 0 分"""
    if not user_answer or len(user_answer.strip()) < 5:
        return GradedAnswer(score=0, is_correct=False,
                            comment="答案过短,未达评判门槛")

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
        comment = f"仅覆盖 {matched}/{len(key_points)} 个关键要点,未达 60% 门槛"

    return GradedAnswer(score=score, is_correct=score > 0, comment=comment)
```

**门槛可调**：默认 60% 起步给分；可在 `.env` 设 `MIN_COVERAGE=0.6` 调整。

**key_points 强约束（v8 新增）**：
- **计算分析 / 综合题** key_points 必须**复用原题**（不得 LLM 现编）
- mixed mode AI 改编时,LLM 输出的 key_points 必须 `set(response.kp) == set(orig.kp)`,**否则视为幻觉拒绝**(adapt_service.py 护栏 2)
- 失败 fallback → 保留原 qdict(`key_points` 用原题)
- 这是 spec v8 改写混合模式**最严的护栏**(fix-20 防幻觉三大护栏之一,见 §13 风险)

**correct_answer 参数注入（v8 fix-25）**：
- 判分时 `correct_answer` 不再直接读 `q.answer`,而是由调用方传入
- mixed mode 启动时,从 attempt_answers.adapted_payload_json 取 `adapted_answer` 字段(若 is_adapted=True)
- 这意味着学员在 mixed mode 改编题作答时,判分**用改编后的答案**判,而不是 DB 原题答案 → 零误判
- standard mode 不变:`correct_answer = q.answer`
- 落地代码 `grader.grade_answer(..., correct_answer=...)` 中

### 6.5 异常处理（判分）

| 异常 | 处理 |
|---|---|
| 学员答案空白（任何题型） | `score = 0, comment = "未作答"` |
| 主观题字符 < 5 | `score = 0, comment = "答案过短，无法评估"` |
| 多选漏选 / 多选 | 选项集合对比，集合相等才算对 |
| key_points 为空（数据缺失） | 主观题退化为"按参考答案完全一致才得分"（兜底） |
| **mixed mode 改编题** correct_answer 缺失 | 退化到 standard 模式用 `q.answer` 兜底判分；attempts 表加 `adapted_payload_json` 持久化（v8 fix-25 P0） |
| **adapt LLM 幻觉** (type 变 / key_points 变 / 答案不等价) | adapter 拒绝,fallback 保留原题；永不瞎编内容写库(详见 §6.4 key_points 强约束) |

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
   │ ◄─── SSE: data: {...} ───────────│ ◄───── chunk N ─────────────────│
   │                                  │                                  │
```

**SSE 事件结构（v8 fix-16 落地完整字段对齐）**：
1. `{done: false, event: "start", question_id: N}` — 流开始标记
2. 多条 `{done: false, event: "delta", delta: "<chars>"}` — LLM 流式碎片
   - 前端累计 `delta` 拼成完整 JSON 字符串（DeepSeek `response_format=json_object`）
   - 解析字段：`available / summary / explanation / key_points / common_pitfalls`
3. 终止事件: `{done: true, available, question_id, reference_answer, analysis, error?}`
   - `available: false` → 前端走 fallback（display `reference_answer + analysis`）
   - 含 `error` 字段 → 同上 fallback,仅显示"讲解服务暂不可用"

#### 6.6.3 System Prompt（讲解唯一 1 套,完整版 v8）

```
你是财务管理科目的本科辅导老师，正在给一位"平时听课一般、基础概念都懂但深度讲解跟不上、
作业基础题能对但应用题吃力、最近在努力补薄弱章节"的学员讲解错题。

【任务】
对学员做错的题目给出讲解，分两种详细程度：
- standard（标准）：覆盖正确答案 + 关键知识点 + 易错点，约 150-250 字
- detailed（详细）：标准 + 公式推导 / 知识拓展 / 类似题型提示，约 300-500 字

【输出 JSON Schema】
{
  "available": true,
  "summary": "≤ 60 字的一句话总结本题考察点",
  "explanation": "讲解正文（standard/detailed 由 level 字段控制）",
  "key_points": ["该题涉及的 3-5 个核心知识点"],
  "common_pitfalls": ["学员最常犯的 1-3 个错误"]
}

【风格要求】
- 中文，平实易懂，避免学术黑话
- 用"咱们/你"称呼学员，不用"该生"
- 公式用 LaTeX 行内（$...$）或独立段落（$$...$$）
- 学员答错时不要指责，直接切入"为什么会这么想 + 正确思路"

【题目元数据】
type: {q_type}
chapter: {chapter_code} — {chapter_title}
difficulty: {difficulty}/3

【题目】
{stem}

【选项】
{options}

【正确答案】
{answer}

【官方参考解析】
{analysis}

【学员答案】
{user_answer}
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
    -- v8:difficulty 由 TEXT('easy'/'medium'/'hard') 改为 INTEGER 1/2/3,与 paper_assembler.difficulty_target 对齐
    difficulty    INTEGER NOT NULL CHECK(difficulty IN (1, 2, 3)),
    stem          TEXT NOT NULL,
    options_json  TEXT,                -- JSON 数组，单选/多选时存；判断题 ['对','错']
    answer        TEXT NOT NULL,      -- 'A' / 'ABD' / '对' / 主观题答案文本
    key_points_json TEXT,             -- JSON 数组，主观题必填;客观题 NULL
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
    total_score           REAL,                       -- 0 ~ 110 (含 10 分综合题)
    score_by_chapter_json TEXT,                        -- JSON {chapter_code: score}
    score_by_type_json    TEXT,                        -- JSON {type: score}
    -- v8:mode 字段记录出题模式(standard/mixed),Dashboard 区分显示 + 排查
    mode                  TEXT NOT NULL DEFAULT 'standard' CHECK(mode IN ('standard','mixed'))
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
    -- v8(关键):mixed mode 改编题持久化 loaded = {
    --   is_adapted, adapted_answer, adapted_key_points, adapted_analysis,
    --   adapted_at (ISO timestamp) }
    -- NULL 表示 standard mode(原题未改编)
    -- 判分逻辑读 attempt_answers.adapted_payload_json → 取 adapted_answer 作 correct_answer 参数注入
    adapted_payload_json TEXT,
    UNIQUE(attempt_id, question_id)
);
```

**schema 演进记录**：
- v6 → v8 核心 DDL 增量：
  - `questions.difficulty`: TEXT('easy'/'medium'/'hard') → INTEGER(1/2/3) — 与 paper_assembler `difficulty_target = {1:0.3, 2:0.5, 3:0.2}` 对齐
  - `exam_attempts.mode`: v8 新增 TEXT DEFAULT 'standard' — 排查 mixed mode 行为
  - `attempt_answers.adapted_payload_json`: v8 新增 TEXT NULLABLE — mixed mode 判分依据
- 迁移路径：Alembic 迁移脚本 `packages/backend/alembic/versions/0002_add_adapted_payload.py`(revision = `0002`)

**comprehensive 槽位 fallback 说明**（运行时透明）：
- `PaperSpec` 定义 `comprehensive: 2 × 10` = 2 个综合题槽位
- 实际题库**只有 4 种题型**（data/parsed 未解析出 comprehensive）
- 运行时 `_pick_from_pool` 在 comprehensive 槽位**自动 fallback 到 `calc`**；题目 `type` 字段**最终为 `calc`**(写库前正常)
- 学员视角看不到差异；spec v6 §6.2 算法约束保持不变

**不入库**：
- 概率分布走 `data/distributions/finance.json`(git 跟踪)
- 讲解内容**不入库**——每次按需生成，结果不持久化(YAGNI 不存"学习历史")

---

## 8. Web API

| 方法 | 路径 | 用途 | 鉴权 | LLM? |
|---|---|---|---|---|
| POST | `/auth/login` | 单密码登录，颁发 JWT | 无 | 否 |
| GET  | `/dashboard` | 历史成绩 + 趋势 + 章节雷达数据 | 用户 JWT | 否 |
| POST | `/exams/start` | 启动一次模拟考（v8 + body `{mode:'standard'\|'mixed'}`），返回 `attempt_id` + 题目 | 用户 JWT | mixed 模式：是(adapt) / standard：否 |
| GET  | `/exams/{id}` | 拉取试卷（断线重连用） | 用户 JWT | 否 |
| DELETE | `/exams/{id}` | 删除一次模拟考（v8 fix-18 新增,级联 attempt_answers） | 用户 JWT | 否 |
| POST | `/exams/{id}/submit` | 交卷，触发判分 pipeline（**mixed 用 adapted_answer 判分**） | 用户 JWT | 否 |
| GET  | `/exams/{id}/result` | 成绩详情 + 每题评语 | 用户 JWT | 否 |
| POST | `/exams/{id}/explain` | 流式讲解某题（SSE） | 用户 JWT | **是**（按需） |
| GET  | `/admin/review/queue` | 开发期：可疑题列表 | 管理员 JWT（独立密码） | 否 |
| POST | `/admin/review/questions/{id}` | 人工修正 / 确认题目 | 管理员 JWT | 否 |

**关键设计**：
- REST + SSE（Server-Sent Events）for 讲解流式输出
- 客观题判分 + 关键词覆盖率判分 = **`submit` 内同步 < 1s**(纯算法;含混合模式 adapted_answer 注入)
- 讲解 = 异步流式，按需触发
- `/admin` 路由**单独鉴权**（独立 JWT + 独立密码），不与考试登录互通

**`/exams/start` 入参（v8 新增 mode 字段）**：
```json
{
  "mode": "standard" | "mixed"
}
```
- 缺省 `standard`，向后兼容
- `mixed` 时后端调 `assemble_paper_async(mode='mixed', deepseek_client=get_deepseek_client())`
- 响应字段 mixed mode 含 `is_adapted / source_question_id / adapted_answer / adapted_key_points / adapted_analysis`（fix-25 P0 必修 透传给前端）

**`DELETE /exams/{id}` 删除行为（v8 fix-18 新增）**：
- 鉴权要求：attempt 的 subject_id 匹配当前登录用户 + JWT
- 级联删除 `attempt_answers`（FK ON DELETE CASCADE）
- 不删 `questions`（题目库只读）
- 返回 204 No Content

**响应体 API 设计（v8 fix-18 fix-19 fix-25 共同对齐）**：
- 所有时间字段 ISO UTC string（如 `"2026-07-05T08:00:00.123Z"`），**前端用 dayjs.utc().tz('Asia/Shanghai') 渲染**
- 所有金额/分数字段用 JSON Number（Python float → JS number）
- 错误响应统一 `{detail: "<msg>"}` 结构（HTTPException 风格）

### 8.1 同步性能保证

| 模式 | /exams/start 耗时 | /exams/{id}/submit 耗时 | 前端 axios timeout |
|---|---|---|---|
| **standard** | < 500ms | < 1s | 15s（默认） |
| **mixed** | p50 ~90s / p95 < 180s / worst case < 300s(实测 144s) | < 1s（同步路径） | **180s**（v8 fix-19 升级） |

---

## 9. 前端页面

```
/login              单密码登录
/                   首页: 最近成绩 + "开始模拟考" 按钮（v8 加模式选择 modal：standard / mixed）
/exam/:id/intro     考试介绍: 时长 / 题型分布 / 章节范围
/exam/:id/play      答题: 分题型分卡片, 单题标记, 顶部计时器
/exam/:id/result    成绩: 总分 + 章节雷达 + 各题评语 + "AI 讲解"按钮
/dashboard          趋势（折线）+ 章节雷达 + 历次成绩列表（v8 加单次删除按钮）
/admin              开发期: 题目 review 界面（独立密码）
```

### 9.1 关键交互

- 答题页用**卡片切题**（不用 long scroll），5 种题型分组卡片
- 顶部计时器（120 分钟倒计时）后端 + 前端双显示
- 交卷前未答题**校验 + 二次确认弹窗**（避免误交）
- 章节雷达图：得分 / 满分 按章节聚合
- 趋势图：历次 `total_score` 折线 + 各章节得分叠加
- **Dashboard 删除按钮**（v8 fix-18）：单次 attempt 行末尾加 el-popconfirm 二次确认;调用 `DELETE /exams/{id}`;成功 toast 提示;失败保留行
- **模式选择 modal**（v8 fix-22,fix-19 调优）：首页点击"开始模拟考" → el-radio-button(standard/mixed)+ 二次确认 → 按 mode 调 /exams/start(mixed axios timeout=180s,standard=15s)
- **AI 讲解按钮**：
  - 结果页每题下方有"AI 讲解"链接（默认折叠，按需点开）
  - 点击后流式接收讲解内容（**fetch + ReadableStream 解析 SSE `data: {...}\n\n`**）
  - 学员答对的题默认折叠，答错的题默认展开
  - 讲解区可点"再讲详细点"重新调用

**401 拦截器 + ?reason=expired（v8 fix-19）**：
- axios 401 响应 → 清 token + 清 role + 跳 `/login?reason=expired`(避免 hard-reload 丢失 store 状态)
- Login.vue onMounted 读 `route.query.reason === 'expired'` → `ElMessage.warning('登录已过期，请重新登录')`

**Home.vue 长时 loading 提示（v8 fix-19）**：
- 选 mixed 模式点击开始 → 30s 后未返回时弹 `ElMessage.info('正在准备混合模式试卷，约需 1-3 分钟，请耐心等待…')`
- 避免用户误以为"卡死 / 失败"

### 9.2 视觉规范（v8 des-1 落地）

**设计令牌 — 雪天 / 蓝天 / 旭日（OKLch）**
- 沉淀于 `packages/frontend/src/styles/tokens.css`
- 配色：
  - **雪** `oklch(98.5% 0.006 230)` — 页面背景/卡片底色
  - **蓝天** `oklch(64% 0.12 232)` — 主品牌色 / 链接 / 按钮主色
  - **旭日** `oklch(80% 0.10 60)` — 强调/通知/警示暖色
- 8 色 chart palette（chart-1 ~ chart-8）— ECharts trend + radar 用
- 圆角节奏：xs 4 / sm 6 / md 10 / lg 14 / xl 20 / pill 999
- 阴影：低饱和 OKLch（不用纯黑）；sky/sun 主题色阴影

**Element Plus 主题覆盖**
- 通过 hex 强制覆盖（spec 约束：Element Plus SCSS 主题变量用 hex 不用 hsl，便于 layered override）
- `packages/frontend/src/styles/element-overrides.css` 集中覆盖

**类名约定**（v8 des-1 沉淀,后续 fix 仅可加,不可删）
- `.qcard` `.option` `.btn-*` `.timer.safe/warn/danger/pulse` `.progress` `.feedback.ok/no` `.stat` `.tag.sky/sun/success/warning/danger` `.note` `.input/.textarea` `.topnav` `.appframe` 等

**Designer Handoff Guardrail（v8 强化）**:
- 后续 phase 改样式**必须 route 回 @designer**,不可由 @fixer 自由改
- @fixer 仅可"机械修改"(保证行为正确 / 类型通过),不可推翻视觉决策
- 落地守护:`pack-frontend/dist/assets/Home-*.js` / `Dashboard-*.js` 设计令牌保留度由 @oracle 抽审

---

## 10. 部署

### 10.1 docker-compose.yml(草案)

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
    # v8:fes TZ=Asia/Shanghai(service 进程内统一 UTC+8;与后端 SQLAlchemy DateTime + 前端 dayjs.tz 渲染对齐)
    environment:
      - TZ=Asia/Shanghai
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 5s
      retries: 3
```

### 10.2 .env.example(提交到 git)

```
DEEPSEEK_API_KEY=replace-me              # 仅 AI 讲解模块使用(运行时唯一 LLM 入口)
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-v4-flash
USER_PASSWORD=replace-me
ADMIN_PASSWORD=replace-me
JWT_SECRET=replace-me-with-random-32-bytes
MIN_COVERAGE=0.6                          # 主观题关键词覆盖率门槛(.env 调整)
JWT_EXPIRE_MINUTES=43200                 # v8:30 天(token 持久时长;延长避免女友考试间隔就过期)
TZ=Asia/Shanghai                          # v8:后端容器 + 前端 dayjs 默认时区统一
```

### 10.3 最小云服务器配置

- 1 vCPU / 1GB RAM(出题+判分是纯算法,零 LLM;仅讲解偶发调用 + mixed mode 启动期)
- 国内节点(女友访问延迟)
- 自动快照(SQLite 一份定时备份)

### 10.4 时区一致性规范(v8 fix-18 落地)

**后端**：
- `os.environ.setdefault('TZ', 'Asia/Shanghai')` + `time.tzset()`(main.py 启动时)
- SQLAlchemy 默认 `DateTime(timezone=True)`;入库时间用 `datetime.now()`(本地时区,UTC+8)
- HTTP 响应时间字段 ISO 字符串统一 UTC:`utcnow_iso()` 工具函数 → `2026-07-05T08:00:00.123Z`

**前端**：
- 所有时间字段前端用 `dayjs.utc(iso).tz('Asia/Shanghai').format('YYYY-MM-DD HH:mm')` 渲染
- `packages/frontend/src/utils/format.ts` 的 `formatDateTime()` 强制 Shanghai 时区(避免本地机器时区差)
- v8 之前 Dashboard 显示时间错乱 bug 已闭环

**为什么不用 SQLite 存 UTC 整数**：
- 当前 v8 入库存 ISO string(可读,简单);前端集中转换
- 后续 v2 可升级为 UTC 整数 + Date 类型,降低 string-parse 开销

---

## 11. 测试策略

| 层 | 工具 | 范围 | 不测 |
|---|---|---|---|
| 解析器 | pytest | 用样本 PDF 验证解析正确率(≥ 95% 字段正确) | LLM 调用 |
| 抽题算法 | pytest | 给定分布+库，验证产出题目数/章节覆盖率/题型比例 | UI |
| 客观题判分 | pytest | 单选/多选/判断边界(漏选、多选、错选) | LLM |
| 主观题判分 | pytest | 关键词覆盖率各类边界(空答/短答/全覆盖/部分覆盖/< 门槛) | LLM |
| API 集成 | pytest + httpx | start → submit → result 全链路(**mock LLM**) | 真 LLM |
| 讲解流式 | pytest + httpx | mock DeepSeek 流式响应，验证 SSE chunk 处理 | 真 LLM |
| **mix 模式测试** | pytest + httpx | **mock LLM 验证 adapted_answer 判分正确**(fix-25 P0)| **真 LLM,真实并发性能** |
| **mix 模式并发性能** | pytest + asyncio | asyncio.gather 并发 13 题时延(p50 < 90s,断言 p95 < 180s / worst case < 300s) | UI |
| 前端组件 | Vitest | 答题卡片 / 计时器 / 雷达图 / AI 讲解按钮 + 401 拦截器 + axios timeout + token 防御(fix-19) | LLM |
| E2E | 手动 + 可选 Playwright | 完整模拟考一次(不查讲解) | 自动跑 |

**铁律**：**LLM 调用一律 mock**。CI / 本地测试**永不**调真实 DeepSeek。

**v8 性能基线（fix-25 已验证,以下为权威三档值,spec 其他章节引用必须对齐）**：

| 端点 / 模式 | p50 | p95 | worst case |
|---|---|---|---|
| standard `/exams/start` | < 50ms | < 200ms | < 500ms |
| mixed `/exams/start` (asyncio.gather × Semaphore=12) | ~90s | < 180s | < 300s（30 题混合） |
| `/exams/{id}/submit` | < 200ms | < 1s | < 2s |

实测样本：N=1 mixed 启动 = 144s(纳入 p50 ~90s 估算)。worst case 30 题双指数级:每题重试 ×2 → < 300s。

---

## 12. DeepSeek API 集成(**运行时 1 role + mixed mode 1 role + 开发期 1 role = 3 role,但 runtime 边界清晰**)

**核心约束**：运行期 LLM 调用**仅服务于**
1. AI 讲解(explain endpoint,学员按需触发)
2. mixed mode 改编(assemble_paper_async mode='mixed',fix-20 + fix-25)

**严禁**运行期 LLM 用于:
- ❌ 出题 standard mode(纯算法)
- ❌ 判分任何环节(零 LLM)
- ❌ 任何"质控"逻辑

### 12.1 模型选择

- **首选**:`deepseek-v4-flash`(用户指定)
- **回退**:`deepseek-chat`(V3 系列稳定版)—— 若 v4-flash 实际不存在时降级
- **实现前必做**:脚本启动时 ping 一次模型列表,确认可用;不可用则自动回退并日志告警

### 12.2 开发期角色:难度评估(一次性,离线)

**唯一允许开发期调 LLM 的环节**——只标 1/2/3,不质疑答案。

```
【角色】
你是财务管理科目的本科学生，正在备考期末考试。
学习画像:
- 平时听课一般——基本概念都懂,但深度讲解跟不上
- 作业有做——基础题能对,应用题吃力
- 最近在努力复习——在补薄弱章节,难题仍在攻克

【任务】
为以下题目标注 1-3 的难度等级(让和你水平差不多的同学参考)。

难度判定标准:
- 1(简单):基础概念记忆 / 直接套公式 / 单一知识点
- 2(中等):综合 2-3 个知识点 / 含应用场景 / 需要小计算
- 3(困难):跨章知识综合 / 含复杂计算 / 需要深度推理

【题目】
题型:${type}
题干:${stem}
${options_or_judge}
正确答案:${answer}
参考解析:${analysis}

【输出 JSON Schema】
{
  "difficulty": 1 | 2 | 3,
  "reasoning": "≤ 50 字说明难度判断依据",
  "knowledge_gaps": ["该题涉及但你这类学生可能未掌握的知识点"]
}
```

**调用方式**:异步批量 ~2s/题 × 200+ 题 ≈ 7 分钟;@oracle 抽 5% 复核。

### 12.3 客户端封装(流式 + 非流式)

```python
class DeepSeekClient:
    """DeepSeek 客户端 — 同时服务 AI 讲解 (流式) + mixed mode 改编 (非流式 JSON)

    v8 加 chat_json_async() 给 mixed mode 改编专用:
    - response_format={"type":"json_object"} 强约束 LLM 输出
    - 单次调用,等全部 JSON 返回
    - 改编完成后由 adapt_service 做 3 重防幻觉校验(type / key_points / 答案等价)
    """

    async def explain_stream(self, system, user, *, timeout=30):
        """流式讲解,asyncio generator,供 /exams/{id}/explain SSE forward"""
        ...

    async def chat_json_async(self, system, user, *, timeout=15):
        """非流式 JSON,供 mixed mode 改编 prompt 用

        - 配置(unconfigured=None) → raise RuntimeError("unconfigured")
        - HTTP 错误 → raise httpx.HTTPError
        - JSON 解析错 → raise JSONDecodeError
        """
        if not self.configured:
            raise RuntimeError("DeepSeek 客户端未配置 API key")
        async with httpx.AsyncClient(timeout=timeout) as c:
            resp = await c.post(...)
            resp.raise_for_status()
            return json.loads(resp.json()["choices"][0]["message"]["content"])
```

### 12.4 成本与频率估算

| 操作 | 触发条件 | 调用次数 |
|---|---|---|
| 出题 standard | 每次模拟考 | 0 |
| 出题 mixed | 学员选 mixed 启动 | ~13 次 LLM/启动(Semaphore 并发 12)|
| 客观题判分 | 自动 | 0 |
| 主观题判分 | 自动 | 0 |
| AI 讲解 | 学员**主动点击**每题讲解按钮 | 0~41 / 模拟考 |

**关键点**：
- 标准模式考完试 = **0 次 LLM 调用**，零成本
- mixed mode 启动 **~13 次 LLM/启动**(实测 144s),单次模拟考混合模式 LLM 成本约 ¥0.01-0.05(短文本)
- 仅在学员想知道某题"为什么"或"再讲详细点"时触发讲解
- 10 元 API 余额 ≈ standard 200+ 次 / mixed 30-50 次(含讲解)

### 12.5 防幻觉护栏(mixed mode 改编)

adapt_service.py 严格执行 spec v8 §6.4 三道护栏。详见 **§6.4 key_points 强约束 + correct_answer 参数注入** 章节。

---

## 13. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| PDF 解析偏移（题干被截断、答案配错选项） | 错题入库 → 女友考试错 | §5.2 机制 3 解析抽样比对 + 机制 4 Agent 团队多轮 + 人工 review |
| Agent 团队审查漏判（领域语义错） | 同上 | §5.2 机制 4 多轮迭代 + 人工全量 review 最后一道关 |
| 主观题判分过严/过松 | 分数偏离学员真实水平 | 关键词覆盖率门槛可调(`.env` MIN_COVERAGE);先人工 review 几场结果再校准 |
| 学员答案全是"标准答案原文"复制（搜索式答题）| 关键词覆盖率虚高 | 后续可加"答案长度检查"+ 答案相似度检测(v2,不在 MVP)|
| AI 讲解超时 | 学员看不到讲解 | 流中断 → 显示"讲解生成失败" + 兜底显示参考答案 + 解析 |
| **mixed mode 启动慢** | 学员等 ~144s(p50)/ <180s(p95),worst case <300s | **asyncio.gather + Semaphore(12)**(fix-25 P0);前端 axios timeout 180s + 30s 后弹"请耐心等"提示(fix-19) |
| **混合模式判分用错答案** | adapted 题目按 DB 原题答案判 → 误判,学员失分 | attempt_answers 加 `adapted_payload_json` 列 + grader `correct_answer` 参数注入(fix-25 P0) + public_qs 响应透传 `is_adapted/adapted_answer`|
| **LLM 出题改编幻觉** | mixed mode LLM 编造新概念/改 key_points/算错答案 → 数据污染 | 详见 **§6.4 防幻觉护栏**（adapt_service 三重校验:type 不变 / key_points 完全复用原题 / 答案等价） |
| **Designer Handoff Guardrail** | 后续 fix 误改 des-1 设计决策 | 视觉变更必须 route @designer;@fixer 仅允许 mechanical fix(§9.2) |
| 客户/女友误改 admin 密码 | 锁死 | `.env` 文件 + git 历史告警（运维提示）|
| 云服务器宕机 | 不可用 | systemd restart + Docker healthcheck + 异地快照 |
| DeepSeek API 涨价 | 成本上升(但很小) | 模型可替换(接口兼容 OpenAI) |
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

> 本节目录树以 `git ls-files` 实际入库为准(commit `a5c4ece` 快照)。任何脚本生成的运行时产物不入 git,后续章节 §10 已说明。

```
finance-exam-system/
├── .dockerignore
├── .env.example                    # JWT_EXPIRE_MINUTES / MIN_COVERAGE / TZ / DeepSeek key
├── .gitignore                      # packages/backend/data/app.db 等运行时 DB 入 ignore
├── README.md
├── docs/
│   └── superpowers/
│       └── specs/
│           └── 2026-07-04-finance-exam-system-design.md   # 本文档(v8)
├── frontend-UI/
│   └── index.html                  # des-1 设计令牌雪天/蓝天/旭日 OKLch + Element Plus 原型沉淀源
├── .slim/
│   └── deepwork/
│       └── finance-exam-system-shipment.md    # 进度跟踪 / phase log / orchestrator handoff
├── data/
│   ├── parsed/                     # 入 git(题目可读 diff)
│   │   ├── questions.jsonl        # 题目库(主,含 key_points)
│   │   ├── questions.bak.jsonl    # 备份
│   │   ├── by_pdf/                # 按 PDF 源文件拆分的章节 jsonl(12 个课件 PDF)
│   │   └── difficulty/
│   │       └── ch{1..9}.jsonl     # §5.1 ②.5 DeepSeek 难度评估结果,每章 1 个
│   ├── distributions/             # 入 git(概率配置)
│   ├── qa/                        # 入 git(QA 报告 + Agent 审查 + Oracle 标记)
│   │   ├── import_report.md
│   │   ├── oracle_review.md       # P3 一致率 88.9%
│   │   └── smoke_test_output.md
│   ├── raw/                       # 不入 git(PDF 中间产物)
│   └── final/
│       └── finance.db             # 不入 git(题库 SQLite,运行时重生成)
├── packages/
│   ├── preprocessor/
│   │   ├── __init__.py
│   │   ├── build_db.py            # 解析 → finance.db 构建
│   │   ├── parse_questions.py     # PDF → jsonl(规则 + 启发式,不调 LLM)
│   │   └── tests/
│   │       └── test_parse_questions.py
│   ├── backend/
│   │   ├── .coverage              # 运行时生成
│   │   ├── .env.example
│   │   ├── .python-version
│   │   ├── README.md
│   │   ├── alembic.ini
│   │   ├── alembic/
│   │   │   ├── README
│   │   │   ├── env.py
│   │   │   ├── script.py.mako
│   │   │   └── versions/
│   │   │       ├── 66ef6d73a07c_init_exam_attempts_and_attempt_answers.py
│   │   │       └── 0002_add_adapted_payload.py   # v8 P0:add adapted_payload_json + mode
│   │   ├── app/
│   │   │   ├── __init__.py
│   │   │   ├── main.py                   # FastAPI app + lifespan + TZ=Asia/Shanghai
│   │   │   ├── config.py                 # Settings:MIN_COVERAGE / JWT_EXPIRE_MINUTES
│   │   │   ├── schemas.py                # Pydantic + StartExamRequest.mode 字段
│   │   │   ├── api/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── auth.py
│   │   │   │   ├── admin.py
│   │   │   │   ├── dashboard.py
│   │   │   │   ├── exams.py              # /exams/* — mode + DELETE + adapted_payload 透传
│   │   │   │   └── explain.py            # /exams/{id}/explain SSE endpoint
│   │   │   ├── models/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── database.py           # 两库分离:finance.db(题库)+ app.db(运行时)
│   │   │   │   ├── question.py           # difficulty INTEGER 1/2/3
│   │   │   │   └── attempt.py            # attempt_answers + adapted_payload_json 列
│   │   │   └── services/
│   │   │       ├── __init__.py
│   │   │       ├── auth_service.py
│   │   │       ├── paper_assembler.py    # assemble() + assemble_paper_async + _mixed_branch
│   │   │       ├── adapt_service.py      # v8:mixed mode 防幻觉改编
│   │   │       ├── grader.py             # grade_answer(correct_answer 参数注入)
│   │   │       └── deepseek_client.py    # 流式 + chat_json_async(双 role)
│   │   ├── data/
│   │   │   └── app.db                  # v8:.gitignore;运行时 SQLite,无 commit
│   │   └── tests/                       # pytest + httpx(全 mock LLM)
│   │       ├── test_adapt_service.py    # v8:3 重护栏 + fallback 单测
│   │       ├── test_adapted_grading.py  # v8:adapted_answer 判分正确性
│   │       ├── test_api.py              # API 集成 + healthcheck
│   │       ├── test_auth.py
│   │       ├── test_concurrent_adapt.py # v8:asyncio.gather 并发性能
│   │       ├── test_grader.py
│   │       ├── test_paper_assembler.py
│   │       └── test_static.py
│   └── frontend/
│       ├── README.md
│       ├── index.html
│       ├── package.json
│       ├── package-lock.json
│       ├── tsconfig.json
│       ├── vite.config.ts
│       ├── vitest.config.ts
│       ├── src/
│       │   ├── main.ts
│       │   ├── App.vue
│       │   ├── api/
│       │   │   ├── client.ts              # axios 401 → /login?reason=expired(fix-19)
│       │   │   ├── index.ts               # startExam timeout 分级 mixed=180s
│       │   │   └── sse.ts                 # fetch + ReadableStream SSE 解析(fix-18)
│       │   ├── components/
│       │   │   ├── ExplainPanel.vue       # AI 讲解流式
│       │   │   ├── QuestionCard.vue       # fix-13 @update:model-value
│       │   │   └── ... (timer、雷达、format 等)
│       │   ├── pages/
│       │   │   ├── Login.vue              # v8:读 ?reason=expired
│       │   │   ├── Home.vue               # v8:模式选择 modal + 30s 长时 loading
│       │   │   ├── ExamIntro.vue
│       │   │   ├── ExamPlay.vue
│       │   │   ├── ExamResult.vue
│       │   │   ├── Dashboard.vue          # v8:删除按钮
│       │   │   └── Admin.vue              # 开发期 /admin review
│       │   ├── router/
│       │   │   └── index.ts               # 鉴权守卫
│       │   ├── stores/                    # Pinia
│       │   │   ├── auth.ts                # JWT 持久化
│       │   │   ├── exam.ts                # v8:startNew token 防御
│       │   │   └── dashboard.ts
│       │   ├── styles/
│       │   │   ├── tokens.css             # des-1:雪天/蓝天/旭日 OKLch
│       │   │   ├── element-overrides.css  # Element Plus 主题(hex)
│       │   │   └── global.css
│       │   ├── types/
│       │   │   └── api.ts
│       │   └── utils/
│       │       └── format.ts              # formatDateTime Asia/Shanghai
│       └── tests/                         # Vitest(v8 41 tests pass)
├── deploy/
│   ├── docker-compose.yml                 # v8:TZ=Asia/Shanghai
│   ├── Dockerfile
│   └── nginx.example.conf
└── openspec/
    └── changes/
        └── finance-exam-system-mvp/      # comet 五阶段产出 delta
```

---

## 16. 实施里程碑

| # | 里程碑 | 产物 | 后续阶段 |
|---|---|---|---|
| M0 | spec 通过 review | 本文件 + git commit | → writing-plans |
| M1 | plan 通过 review | plans/*.md + git commit | → /comet-open |
| M2 | 资料预处理通过 | `data/parsed/questions.jsonl` + key_points 完整 + difficulty 标注完整 + Agent 团队多轮审查通过 + 用户人工 review 100% | → 接入后端 |
| M2a | PDF 探索 + 解析 | @explorer 浏览 12 PDF 结构 + @fixer 解析器输出 jsonl | → 进入难度评估 |
| M2b | DeepSeek 难度评估 | @fixer 批量异步调用 DeepSeek，~200 题/7min，jsonl.difficulty 字段填充 | → 抽样复核 |
| M2c | Agent 多轮 + 人工 review | @oracle 架构+领域双轮 + @explorer 完整性 + /admin 人工 | → 接入后端 |
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
| 2 | LLM 选型 | DeepSeek-v4-flash（运行期**AI 讲解** + **mixed mode 改编**） | 用户 |
| 3 | 判分方式 | **客观自动 + 主观关键词覆盖率**（零 LLM） | 用户 |
| 4 | 模式 | 模拟考（**v8 新增 standard / mixed 双模式**） | 用户 + 设计 |
| 5 | 试卷结构 | 单 15×2 + 多 10×3 + 判 10×1 + 计 4×5 + 综 2×10 = 100 分 / 120 分钟 | 用户 |
| 6 | AI 反馈详细度 | **AI 讲解**（学员按需触发）+ 判分评语（关键词覆盖率结果） | 用户 |
| 7 | 历史保存 | 历次成绩 + 作答明细 + 趋势 + 雷达图 | 用户 |
| 8 | 预处理质量兜底 | **Agent 团队多轮迭代 + 用户人工全量 review**（**不调 DeepSeek**） | 用户 |
| 9 | DeepSeek 边界 | **运行期讲解 + mixed mode 改编**；开发期不参与预处理任何环节；standard mode 出题 / 判分均不调 LLM | 用户 |
| 10 | 资料性质 | **题目+答案组合**（预处理任务 = 结构化提取 + 校验 + key_points 标注） | 用户 |
| 11 | 运行时出题 | **standard mode 纯算法随机抽样**(章节×题型×难度加权);**v8 增加 mixed mode**(AI 改编 ~30% 题,严格防幻觉) | 用户 + v8 fix-20 |
| 12 | 主观题判分依据 | **key_points 关键词覆盖率**（≥60% 起步按比例给分）;**v8:key_points 复用原题(强约束)** | 用户 + v8 fix-20 |
| 13 | AI 讲解触发 | 学员**主动点击**（失败时显示参考答案 + 解析兜底） | 用户 |
| 14 | 难度评估方式 | **DeepSeek 角色扮演**（财务管理普通学生），输出 1/2/3 等级 | 用户 |
| 15 | 开发期 LLM 边界 | **仅难度评估**（不参与答案校验 / 解析完整性 / 章节归属判断） | 用户（设计原则）|
| 16 | 难度对抽样的影响 | 影响出题分布权重（30% 易 + 50% 中 + 20% 难），不影响判分 | 用户 |
| **17** | **adapted_payload 持久化** | mixed mode 改编题在 `attempt_answers.adapted_payload_json` 列持久化 `is_adapted / adapted_answer / adapted_key_points / adapted_analysis`;grader `correct_answer` 参数注入,**判分用 adapted_answer**,杜绝 100% 误判 | oracle + fix-25 P0 |
| **18** | **mixed mode 并发改造** | `asyncio.gather + Semaphore(12)` 并发改编 13 题;实测 p50 ~90s / p95 < 180s(worst case < 300s);前端 axios timeout 180s | fix-25 P0 |
| **19** | **Designer Handoff Guardrail** | 视觉/交互变更由 `@designer` 独占;`@fixer` 不可推翻设计决策(仅可机械修改);设计令牌(tokens.css/element-overrides.css)沉淀在 frontend/src/styles/ | des-1 + oracle |
| **20** | **前端 SSE_BASE 修复** | axios `baseURL` 在 dev 用 `/api`,prod 空字符串;`fetchSSE` 同样依据 `import.meta.env.DEV`;否则 SSE 在 prod 404 | fix-16 |
| **21** | **axios timeout 分级** | standard mode 15s(默认);**mixed mode 180s**(fix-19 覆盖 worst case p95,见 §11 三档表);Home.vue 长时 loading 30s 后弹提示避免误判"卡死" | fix-19 |

---

_文档结束。本 spec v8 已与代码 a5c4ece (fix-12 ~ fix-25) 完全对齐,等待 `/comet-archive` 时同步到 main spec 并归档 delta。_

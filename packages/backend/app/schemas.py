"""Pydantic schema 定义（API 层）。

所有 schema 使用 ConfigDict(extra='forbid') 严格模式。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------- 题型 / 难度 / 角色 ----------

# fix-30 P0 扩展:新增 short_answer / case_analysis 两种题型（公司战略和风险管理科目）。
# 数据库层 SQLAlchemy 用 Text 无 CHECK 约束,故新字面量无需迁移 schema。
QuestionType = Literal[
    "single", "multi", "judge", "calc", "comprehensive", "short_answer", "case_analysis"
]
DifficultyLevel = Literal[1, 2, 3]
UserRole = Literal["user", "admin"]


# ---------- Subjects ----------


class SubjectOut(BaseModel):
    """科目列表响应 — 前端 SubjectSwitcher 下拉选项。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    question_count: int


# ---------- Auth ----------


class LoginRequest(BaseModel):
    """登录请求体。"""

    model_config = ConfigDict(extra="forbid")

    password: str = Field(..., min_length=1, max_length=128)


class LoginResponse(BaseModel):
    """登录响应：JWT + 角色。"""

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    role: UserRole
    expires_in: int  # 秒


# ---------- Question ----------


class CaseSubQuestion(BaseModel):
    """案例分析题子问题评分标准。

    fix-30 P1:案例分析题由多 sub_question + 一个 conclusion 组成,
    每个 sub_question 有独立的 key_points 列表和满分值。
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, max_length=8, description="子问题编号,如 '1'、'2a'")
    points: float = Field(..., ge=0, description="该子问题满分值")
    key_points: list[str] = Field(default_factory=list, description="该子问题的关键要点列表")
    weight: float | None = Field(
        default=None, ge=0, le=1, description="子问题权重(0-1,可选;若提供则用于加权)"
    )


class CaseConclusion(BaseModel):
    """案例分析题结论部分评分标准。

    fix-30 P1:案例分析题整体结论部分,独立打分(不与 sub_question 加权混合)。
    """

    model_config = ConfigDict(extra="forbid")

    points: float = Field(..., ge=0, description="结论部分满分值")
    criteria: list[str] = Field(default_factory=list, description="结论要点列表")


class CaseRubric(BaseModel):
    """案例分析题结构化评分 rubric(兼容保留 — 既有 grader/导出代码可能引用)。

    fix-30 P1:由 AI 出题 agent 在生成题目时构造,存为题目元数据(JSON);
    判分时 grader 按 sub_question 逐项打分 + 单独 conclusion 打分。

    结构示例:
        {
            "sub_questions": [
                {"id": "1", "points": 3, "key_points": ["SWOT", "PEST"], "weight": 0.3},
                {"id": "2", "points": 4, "key_points": ["战略选择"], "weight": 0.4}
            ],
            "conclusion": {"points": 3, "criteria": ["总结性结论", "可执行建议"]}
        }
    注:CaseRubric.conclusion 沿用历史 `criteria` 字段命名。
    """

    model_config = ConfigDict(extra="forbid")

    sub_questions: list[CaseSubQuestion] = Field(default_factory=list)
    conclusion: CaseConclusion | None = None

    def total_points(self) -> float:
        """计算 rubric 总满分(sub_questions + conclusion)。"""
        total = sum(sq.points for sq in self.sub_questions)
        if self.conclusion is not None:
            total += self.conclusion.points
        return total


class RubricItem(BaseModel):
    """单条 rubric 评分单元(Phase 1.3a 统一版)。

    用于 QuestionRubric.sub_questions / QuestionRubric.conclusion,
    与 fix-31 前端 QuestionRubric.criteria 对齐 — 统一用 key_points 字段命名
    (语义同前端 criteria:list[str])。

    字段约定(对齐前端 + spec):
        id:str       — 子问题编号,如 '1'、'2a'、'conclusion'
        points:float — 该子项满分值
        key_points:  — 关键要点列表(用作判分依据)
        weight:float — 子项权重,默认 1.0(可加权)
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, max_length=16, description="子项编号,如 '1'、'2a'")
    points: float = Field(..., ge=0, description="该子项满分值")
    key_points: list[str] = Field(default_factory=list, description="关键要点列表(等同前端 criteria)")
    weight: float = Field(default=1.0, ge=0, le=1, description="子项权重(0-1)")


class QuestionRubric(BaseModel):
    """案例分析题结构化评分 rubric(Phase 1.3a 统一版,前端 QuestionRubric 对齐)。

    替换前 dict-typed rubric 字段,提供强类型 + model_dump 序列化。
    用于 QuestionPublic.rubric(仅 case_analysis 题型有值,其他题型为 None)。

    结构:
        {
            "sub_questions": [RubricItem, ...],
            "conclusion":    RubricItem | None
        }

    对齐 fix-31 前端 QuestionRubric interface:
        - 后端 RubricItem.key_points  == 前端 criterion string[]
        - 前端 import 时把 `criteria` 映射为 `key_points`(Ponytail: 文档层映射)
        - 序列化只导出 key_points(后端是权威数据源)
    """

    model_config = ConfigDict(extra="forbid")

    sub_questions: list[RubricItem] = Field(default_factory=list)
    conclusion: RubricItem | None = None

    def total_points(self) -> float:
        """计算 rubric 总满分(sub_questions + conclusion)。"""
        total = sum(item.points for item in self.sub_questions)
        if self.conclusion is not None:
            total += self.conclusion.points
        return total


class QuestionPublic(BaseModel):
    """题目（学员视图，隐藏 answer/key_points）。

    fix-22 P0 修复:新增 `is_adapted` / `source_question_id` 字段,
    让前端可识别混合模式下的 AI 改编题(UI 标注 + 答案比对)。

    fix-30 P1 扩展:新增 `rubric` 字段,仅 case_analysis 题型有值;
    前端按 type 决定是否渲染子问题输入区。

    Phase 1.3a:rubric 字段类型从 `dict[str, Any] | None` 改为 `QuestionRubric | None`,
    强类型 + model_dump JSON 序列化。后端是 key_points 数据源,前端 mapping 函数把
    key_points ↔ criteria 对齐。
    """

    model_config = ConfigDict(extra="forbid")

    id: int
    type: QuestionType
    chapter_id: int
    chapter_code: str
    difficulty: DifficultyLevel
    stem: str
    options: list[str] | None = None
    score: float  # full_score
    sequence: int  # 试卷中的序号
    # fix-22: 混合模式 AI 改编标记
    is_adapted: bool = False  # True → 此题为 AI 改编(基于 source_question_id)
    source_question_id: int | None = None  # 改编时为原题 id;非改编时为 None
    # Phase 1.3a:案例分析题结构化评分 rubric(仅 case_analysis 有值;其他题型 None)
    rubric: QuestionRubric | None = None


class QuestionWithAnswer(QuestionPublic):
    """题目（管理员视图，含答案 + 解析 + key_points）。"""

    answer: str
    key_points: list[str] | None = None
    analysis: str | None = None
    source_pdf: str
    page_ref: int | None = None


# ---------- Exam ----------


class StartExamRequest(BaseModel):
    """启动考试请求 — fix-23a 必填 `subject_id`，fix-22 新增 `mode` 字段。

    `subject_id` 控制学科隔离（多科目支持）：'fin-mgmt' | 'corp-strat' | 后续新增。
    学科 ID 由调用方传入，后端做存在性校验（不存在 → 400）。

    `mode` 控制出题策略：
    - `standard`（默认）：走原章节×题型×难度加权抽样
    - `mixed`：混合模式（fix-20 实现，~30% 题 AI 改编）
    """

    model_config = ConfigDict(extra="forbid")

    subject_id: str = Field(..., min_length=1, description="学科 ID（必填）")
    mode: Literal["standard", "mixed"] = Field(default="standard")


class Paper(BaseModel):
    """组卷结果 — fix-23a P0 critical partial-fill 支持。

    字段:
        questions: 出题列表(可能少于 spec 要求,见 partial/returned)。
            type=list[dict[str, Any]] 而非 list[QuestionPublic]:
            保留混合模式的 adapted_* 字段(adapted_answer/adapted_key_points/
            adapted_analysis),供 exams.py 持久化到 attempt_answers.adapted_payload_json
            (submit/result 端点用 adapted_answer 判分)。
        partial: 是否为部分组卷（题库题数 < spec 要求时为 True）
        requested: spec 期望的题数(None 表示无 spec)
        returned: 实际返回的题数（== len(questions)）
    """

    model_config = ConfigDict(extra="forbid")

    questions: list[dict[str, Any]] = Field(default_factory=list)
    partial: bool = False
    requested: int | None = None
    returned: int | None = None


class StartExamResponse(BaseModel):
    """启动考试响应。

    fix-23a P0 critical:新增 `paper` + `info_msg` 字段,传递 partial-fill 元信息。
    - `paper`:组卷结果(含 partial/requested/returned 字段)
    - `info_msg`:人工可读提示(部分组卷时说明,例如
      "题库仅含 X 题, 部分组卷, Y 标准题已出")
    - `questions`:保留作为 top-level 字段,前端向后兼容
    """

    attempt_id: int
    started_at: str
    time_limit_minutes: int
    questions: list[QuestionPublic]
    total_score: float
    paper: Paper | None = None
    info_msg: str | None = None


class ExamSnapshot(BaseModel):
    """考试快照（断线重连用）。"""

    model_config = ConfigDict(extra="forbid")

    attempt_id: int
    started_at: str
    time_limit_minutes: int
    submitted_at: str | None
    questions: list[QuestionPublic]
    answers: dict[int, str] = Field(default_factory=dict)  # question_id → user_answer


class SubmitAnswerItem(BaseModel):
    """提交答案的单题条目。"""

    model_config = ConfigDict(extra="forbid")

    question_id: int
    user_answer: str = Field(default="")


class SubmitExamRequest(BaseModel):
    """交卷请求。"""

    model_config = ConfigDict(extra="forbid")

    answers: list[SubmitAnswerItem]


class GradedAnswerDetail(BaseModel):
    """单题判分详情。"""

    model_config = ConfigDict(extra="forbid")

    question_id: int
    sequence: int
    type: QuestionType
    chapter_code: str
    stem: str
    user_answer: str
    correct_answer: str
    is_correct: bool | None
    awarded_score: float
    full_score: float
    comment: str
    # 主观题扩展字段（仅 calc/comprehensive 有值，客观题为 None）
    sub_answer_count: int | None = None  # 识别到的分小问作答数（≥ 2 时填）
    missed_points: list[str] | None = None  # 未覆盖关键要点（最多 3 条）
    # fix-23a:题目选项列表（仅 single/multi 有值；judge/calc/comprehensive
    # /short_answer/case_analysis 为 None）。结果页用于展示选项 + 标 ✓ 正确项。
    options: list[str] | None = None


class SubmitExamResponse(BaseModel):
    """交卷响应（含每题评语）。"""

    attempt_id: int
    total_score: float
    score_by_chapter: dict[str, float]
    score_by_type: dict[str, float]
    answers: list[GradedAnswerDetail]
    submitted_at: str


class ExamResult(BaseModel):
    """成绩详情（result 接口）。"""

    model_config = ConfigDict(extra="forbid")

    attempt_id: int
    started_at: str
    submitted_at: str | None
    total_score: float
    score_by_chapter: dict[str, float]
    score_by_type: dict[str, float]
    answers: list[GradedAnswerDetail]


# ---------- Dashboard ----------


class AttemptSummary(BaseModel):
    """单次成绩摘要。"""

    model_config = ConfigDict(extra="forbid")

    attempt_id: int
    started_at: str
    submitted_at: str | None
    total_score: float


class DashboardResponse(BaseModel):
    """仪表盘数据。"""

    model_config = ConfigDict(extra="forbid")

    history: list[AttemptSummary]
    score_trend: list[float]  # 历次总分
    chapter_radar: dict[str, float]  # {chapter_code: avg_score}
    total_attempts: int


# ---------- Admin ----------


class ReviewQueueItem(BaseModel):
    """review 队列条目。"""

    model_config = ConfigDict(extra="forbid")

    id: int
    type: QuestionType
    chapter_code: str
    difficulty: DifficultyLevel
    stem: str
    answer: str
    key_points: list[str] | None = None
    flags: list[str] = Field(default_factory=list)  # 风险标签（开发期）


class ReviewQueueResponse(BaseModel):
    """review 队列响应。"""

    items: list[ReviewQueueItem]
    total: int


class ReviewUpdateRequest(BaseModel):
    """人工修正题目请求。"""

    model_config = ConfigDict(extra="forbid")

    answer: str | None = None
    key_points: list[str] | None = None
    analysis: str | None = None
    difficulty: DifficultyLevel | None = None


class ReviewUpdateResponse(BaseModel):
    """人工修正响应。"""

    question_id: int
    updated_fields: list[str]


# ---------- Explain (占位) ----------


class ExplainRequest(BaseModel):
    """讲解请求。"""

    model_config = ConfigDict(extra="forbid")

    question_id: int
    level: Literal["standard", "detailed"] = "standard"


class ExplainResponse(BaseModel):
    """讲解响应（占位 stub）。"""

    question_id: int
    available: bool
    explanation: str
    reference_answer: str
    analysis: str | None = None


# ---------- Health ----------


class HealthResponse(BaseModel):
    """健康检查响应。"""

    status: Literal["ok", "degraded"]
    database: bool
    question_count: int | None = None
    app_name: str


# ---------- Helper ----------


def to_json(obj: Any) -> str:
    """通用 JSON 序列化（应用库存储用）。"""
    import json

    return json.dumps(obj, ensure_ascii=False)


def from_json(s: str | None, default: Any = None) -> Any:
    """通用 JSON 反序列化。"""
    import json

    if not s:
        return default
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return default


def utcnow_iso() -> str:
    """UTC ISO 时间字符串。"""
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"
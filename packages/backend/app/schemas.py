"""Pydantic schema 定义（API 层）。

所有 schema 使用 ConfigDict(extra='forbid') 严格模式。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------- 题型 / 难度 / 角色 ----------

QuestionType = Literal["single", "multi", "judge", "calc", "comprehensive"]
DifficultyLevel = Literal[1, 2, 3]
UserRole = Literal["user", "admin"]


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


class QuestionPublic(BaseModel):
    """题目（学员视图，隐藏 answer/key_points）。

    fix-22 P0 修复：新增 `is_adapted` / `source_question_id` 字段，
    让前端可识别混合模式下的 AI 改编题（UI 标注 + 答案比对）。
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
    is_adapted: bool = False  # True → 此题为 AI 改编（基于 source_question_id）
    source_question_id: int | None = None  # 改编时为原题 id；非改编时为 None


class QuestionWithAnswer(QuestionPublic):
    """题目（管理员视图，含答案 + 解析 + key_points）。"""

    answer: str
    key_points: list[str] | None = None
    analysis: str | None = None
    source_pdf: str
    page_ref: int | None = None


# ---------- Exam ----------


class StartExamRequest(BaseModel):
    """启动考试请求（当前无 body，预留扩展）。"""

    model_config = ConfigDict(extra="forbid")


class StartExamResponse(BaseModel):
    """启动考试响应。"""

    attempt_id: int
    started_at: str
    time_limit_minutes: int
    questions: list[QuestionPublic]
    total_score: float


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
"""应用库 ORM 模型（exam_attempts / attempt_answers）。

按 spec §7 schema 严格对齐。
"""
from __future__ import annotations

from sqlalchemy import (
    Float,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.database import Base


class ExamAttempt(Base):
    """一次模拟考记录。"""

    __tablename__ = "exam_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subject_id: Mapped[str] = mapped_column(
        Text, ForeignKey("subjects.id"), nullable=False
    )
    started_at: Mapped[str] = mapped_column(Text, nullable=False)
    submitted_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # JSON: {chapter_code: score}
    score_by_chapter_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON: {type: score}
    score_by_type_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON: 题目 ID 顺序（用于断线重连）— 简化为序列化的 int 列表字符串
    question_sequence_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    answers: Mapped[list["AttemptAnswer"]] = relationship(
        back_populates="attempt", cascade="all, delete-orphan"
    )


class AttemptAnswer(Base):
    """一次模拟考中每题的作答与判分记录。

    fix-22 P0 关键修复：
    - `adapted_payload_json` 字段持久化 AI 改编 payload（混合模式）。
      含 `is_adapted / adapted_answer / adapted_key_points / adapted_analysis`，
      submit/result 端点用 `adapted_answer` 替换原题答案判分，
      避免"混合模式 100% 误判"的 critical bug。
    - 列 nullable=True：原题（standard / 未改编）保持 NULL，向后兼容。
    """

    __tablename__ = "attempt_answers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    attempt_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("exam_attempts.id"), nullable=False
    )
    question_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("questions.id"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    user_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_correct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    awarded_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    grading_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON: 改编 payload（仅混合模式改编题有值；原题 NULL）。
    # 含 is_adapted / source_question_id / adapted_answer / adapted_key_points / adapted_analysis
    adapted_payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (UniqueConstraint("attempt_id", "question_id", name="uq_attempt_question"),)

    attempt: Mapped[ExamAttempt] = relationship(back_populates="answers")
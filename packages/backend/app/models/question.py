"""题目库 ORM 模型（subjects / chapters / questions）。"""
from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.database import Base


class Subject(Base):
    """学科表。"""

    __tablename__ = "subjects"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)

    chapters: Mapped[list["Chapter"]] = relationship(
        back_populates="subject", cascade="all, delete-orphan"
    )


class Chapter(Base):
    """章节表。"""

    __tablename__ = "chapters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subject_id: Mapped[str] = mapped_column(
        Text, ForeignKey("subjects.id"), nullable=False
    )
    code: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    weight: Mapped[float] = mapped_column(default=1.0, nullable=False)

    subject: Mapped[Subject] = relationship(back_populates="chapters")
    questions: Mapped[list["Question"]] = relationship(back_populates="chapter")


class Question(Base):
    """题目表（对应预处理入库存量）。"""

    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subject_id: Mapped[str] = mapped_column(
        Text, ForeignKey("subjects.id"), nullable=False
    )
    chapter_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("chapters.id"), nullable=False
    )
    type: Mapped[str] = mapped_column(Text, nullable=False)
    # difficulty 在数据库为 INTEGER（1/2/3），对应 easy/medium/hard
    difficulty: Mapped[int] = mapped_column(Integer, nullable=False)
    stem: Mapped[str] = mapped_column(Text, nullable=False)
    options_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    key_points_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    analysis: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_pdf: Mapped[str] = mapped_column(Text, nullable=False)
    page_ref: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="CURRENT_TIMESTAMP"
    )

    chapter: Mapped[Chapter] = relationship(back_populates="questions")
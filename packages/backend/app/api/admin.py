"""Admin API — 题目 review（开发期，admin 鉴权）。

按 spec §8：
- GET /admin/review/queue: 可疑题列表
- POST /admin/review/questions/{id}: 人工修正
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app.api.auth import get_admin_user
from app.models.database import get_session_factory
from app.models.question import Chapter, Question
from app.schemas import (
    ReviewQueueItem,
    ReviewQueueResponse,
    ReviewUpdateRequest,
    ReviewUpdateResponse,
    to_json,
)
from app.services.grader import parse_key_points

router = APIRouter(prefix="/admin", tags=["admin"])


def _flag_question(q: Question, kp: list[str] | None) -> list[str]:
    """根据规则给题目打风险标签（开发期简单规则）。"""
    flags: list[str] = []
    if not q.analysis:
        flags.append("缺少解析")
    if q.type in ("calc", "comprehensive") and not kp:
        flags.append("主观题缺 key_points")
    if not q.options_json and q.type in ("single", "multi"):
        flags.append("客观题缺 options")
    if not q.answer:
        flags.append("缺少答案")
    return flags


@router.get("/review/queue", response_model=ReviewQueueResponse)
async def review_queue(
    _admin: Annotated[str, Depends(get_admin_user)],
) -> ReviewQueueResponse:
    """开发期：可疑题列表。"""
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(Question, Chapter.code)
            .join(Chapter, Chapter.id == Question.chapter_id)
            .order_by(Question.id.asc())
        )
        items: list[ReviewQueueItem] = []
        for q, ch_code in result.all():
            kp = parse_key_points(q.key_points_json)
            flags = _flag_question(q, kp)
            if flags:
                items.append(
                    ReviewQueueItem(
                        id=q.id,
                        type=q.type,  # type: ignore[arg-type]
                        chapter_code=ch_code,
                        difficulty=q.difficulty,  # type: ignore[arg-type]
                        stem=q.stem,
                        answer=q.answer,
                        key_points=kp,
                        flags=flags,
                    )
                )
    return ReviewQueueResponse(items=items, total=len(items))


@router.post("/review/questions/{question_id}", response_model=ReviewUpdateResponse)
async def update_question(
    question_id: int,
    req: ReviewUpdateRequest,
    _admin: Annotated[str, Depends(get_admin_user)],
) -> ReviewUpdateResponse:
    """开发期：人工修正题目。"""
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(Question).where(Question.id == question_id)
        )
        q = result.scalar_one_or_none()
        if q is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="题目不存在")

        updated: list[str] = []
        if req.answer is not None:
            q.answer = req.answer
            updated.append("answer")
        if req.key_points is not None:
            q.key_points_json = to_json(req.key_points)
            updated.append("key_points")
        if req.analysis is not None:
            q.analysis = req.analysis
            updated.append("analysis")
        if req.difficulty is not None:
            q.difficulty = req.difficulty
            updated.append("difficulty")

        await session.commit()
    return ReviewUpdateResponse(question_id=question_id, updated_fields=updated)
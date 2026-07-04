"""仪表盘 API — 历次成绩 + 趋势 + 章节雷达。"""
from __future__ import annotations

from collections import defaultdict
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.models.attempt import ExamAttempt
from app.models.database import get_app_session_factory
from app.schemas import (
    AttemptSummary,
    DashboardResponse,
    from_json,
)

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard", response_model=DashboardResponse)
async def get_dashboard(
    _user: Annotated[str, Depends(get_current_user)],
) -> DashboardResponse:
    """仪表盘 — 历次成绩 + 趋势 + 章节雷达数据。

    章节雷达 = 各章节历次平均分（仅含已交卷）。
    """
    factory = get_app_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(ExamAttempt)
            .where(ExamAttempt.submitted_at.is_not(None))
            .order_by(ExamAttempt.started_at.asc())
        )
        attempts = list(result.scalars().all())

    history: list[AttemptSummary] = []
    score_trend: list[float] = []
    chapter_score_sum: dict[str, float] = defaultdict(float)
    chapter_score_count: dict[str, int] = defaultdict(int)

    for a in attempts:
        history.append(
            AttemptSummary(
                attempt_id=a.id,
                started_at=a.started_at,
                submitted_at=a.submitted_at,
                total_score=a.total_score or 0.0,
            )
        )
        score_trend.append(a.total_score or 0.0)
        # 章节分聚合
        sc = from_json(a.score_by_chapter_json, {})
        if isinstance(sc, dict):
            for ch, s in sc.items():
                chapter_score_sum[ch] += float(s)
                chapter_score_count[ch] += 1

    chapter_radar = {
        ch: round(chapter_score_sum[ch] / chapter_score_count[ch], 2)
        for ch in chapter_score_sum
    }

    return DashboardResponse(
        history=history,
        score_trend=score_trend,
        chapter_radar=chapter_radar,
        total_attempts=len(attempts),
    )
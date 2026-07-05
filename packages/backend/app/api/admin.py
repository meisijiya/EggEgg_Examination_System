"""Admin API — 题目 review(开发期,admin 鉴权)。

按 spec §8:
- GET /admin/review/queue: 可疑题列表
- POST /admin/review/questions/{id}: 人工修正

Phase 1.2 新增(AI 出题入库 gate — oracle P0 critical):
- GET /admin/ai-generated-questions?status=pending: AI 出题待人工 review 列表
- POST /admin/approve-question/{question_id}: approve(改 status='approved')
- POST /admin/reject-question/{question_id}: reject(改 status='rejected' + 理由)
- POST /admin/register-approved-questions: 注册已 approved 的题目到 SQLite 题库
  (build_db.py 加载时只认 status='approved', 防 oracle critical 兜底)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.api.auth import get_admin_user
from app.config import get_settings
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

logger = logging.getLogger("fes.admin")

router = APIRouter(prefix="/admin", tags=["admin"])

# ---------------------------------------------------------------------------
# AI 出题 JSONL 路径(Phase 1.2 新增)
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[3]
AI_GENERATED_JSONL = PROJECT_ROOT / "data" / "parsed" / "corporate_strategy_ai_generated.jsonl"

# ---------------------------------------------------------------------------
# 请求 / 响应 Schema(Phase 1.2 新增)
# ---------------------------------------------------------------------------


class AIQuestionItem(BaseModel):
    """AI-generated 题目条目(从 JSONL 反序列化 / 序列化)。"""

    model_config = ConfigDict(extra="ignore")  # 允许 AI 出题额外字段通过

    id: str
    source_ref: dict[str, Any] = Field(default_factory=dict)
    type: str
    stem: str = ""
    options: list[str] | None = None
    answer: str = ""
    key_points: list[str] | None = None
    analysis: str | None = None
    difficulty: int = 2
    ai_generated: bool = True
    confidence: float = 0.0
    needs_manual_review: bool = False
    status: Literal["pending", "approved", "rejected"] = "pending"
    review_reason: str | None = None
    web_evidence: list[str] = Field(default_factory=list)


class AIQuestionListResponse(BaseModel):
    """GET /admin/ai-generated-questions 响应。"""

    model_config = ConfigDict(extra="forbid")

    items: list[AIQuestionItem]
    total: int


class ReviewDecisionRequest(BaseModel):
    """POST /admin/approve-question 或 reject-question body。"""

    model_config = ConfigDict(extra="forbid")

    review_note: str | None = Field(default=None, description="人工 review 备注(可选)")


class ReviewDecisionResponse(BaseModel):
    """POST /admin/approve-question / reject-question 响应。"""

    model_config = ConfigDict(extra="forbid")

    question_id: str
    new_status: Literal["approved", "rejected"]
    review_note: str | None = None
    reviewed_at: str


# ---------------------------------------------------------------------------
# 现有 review endpoints(沿用)
# ---------------------------------------------------------------------------


def _flag_question(q: Question, kp: list[str] | None) -> list[str]:
    """根据规则给题目打风险标签(开发期简单规则)。"""
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
    """开发期:可疑题列表。"""
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
    """开发期:人工修正题目。"""
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


# ---------------------------------------------------------------------------
# Phase 1.2 新增: AI 出题 review gates
# ---------------------------------------------------------------------------


def _read_ai_jsonl(path: Path | None = None) -> list[dict[str, Any]]:
    """读 AI 出题 JSONL → list of dicts(行级 dict,容忍 schema 漂移)。

    path 参数化让测试隔离(tmp file) + monkeypatch AI_GENERATED_JSONL ——
    调用点处每次取最新值。
    """
    path = path or AI_GENERATED_JSONL
    if not path.exists():
        return []
    items: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for ln, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError as e:
                logger.warning("AI JSONL 第 %d 行 JSON 解析失败: %s", ln, e)
    return items


def _write_ai_jsonl(
    items: list[dict[str, Any]], path: Path | None = None
) -> int:
    """写回 AI 出题 JSONL(原子替换)。返回写入行数。"""
    path = path or AI_GENERATED_JSONL
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    tmp.replace(path)
    return len(items)


@router.get("/ai-generated-questions", response_model=AIQuestionListResponse)
async def list_ai_questions(
    _admin: Annotated[str, Depends(get_admin_user)],
    status_filter: Literal["pending", "approved", "rejected", "all"] = Query(
        "pending", alias="status"
    ),
) -> AIQuestionListResponse:
    """列出 AI 出题条目(默认 pending=待人工 review)。

    Query 参数:
      - status=pending|approved|rejected|all
    """
    items_raw = _read_ai_jsonl()
    if status_filter != "all":
        items_raw = [it for it in items_raw if it.get("status") == status_filter]
    items = [AIQuestionItem(**it) for it in items_raw]
    return AIQuestionListResponse(items=items, total=len(items))


def _set_item_status(
    question_id: str,
    new_status: Literal["approved", "rejected"],
    review_note: str | None = None,
) -> ReviewDecisionResponse:
    """在 JSONL 中定位并修改指定 question 的 status 字段(原子写)。"""
    items = _read_ai_jsonl()
    found = False
    now = datetime.utcnow().isoformat() + "Z"
    for it in items:
        if it.get("id") == question_id:
            it["status"] = new_status
            if review_note:
                it["review_reason"] = review_note
            it["reviewed_at"] = now
            found = True
            break
    if not found:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"AI 题目 id={question_id!r} 未找到",
        )
    _write_ai_jsonl(items)
    logger.info("admin: AI 题目 %s → %s (note=%s)", question_id, new_status, review_note)
    return ReviewDecisionResponse(
        question_id=question_id,
        new_status=new_status,
        review_note=review_note,
        reviewed_at=now,
    )


@router.post("/approve-question/{question_id}", response_model=ReviewDecisionResponse)
async def approve_ai_question(
    question_id: str,
    req: ReviewDecisionRequest,
    _admin: Annotated[str, Depends(get_admin_user)],
) -> ReviewDecisionResponse:
    """approve 一个 AI 题目(改 status='approved')。

    后续 build_db.py 加载 AI JSONL 入 SQLite 时只接受 status='approved' 的题目,
    实现 oracle P0 critical 兜底 — 任何 AI 出题入库必须经人工 review。
    """
    return _set_item_status(question_id, "approved", req.review_note)


@router.post("/reject-question/{question_id}", response_model=ReviewDecisionResponse)
async def reject_ai_question(
    question_id: str,
    req: ReviewDecisionRequest,
    _admin: Annotated[str, Depends(get_admin_user)],
) -> ReviewDecisionResponse:
    """reject 一个 AI 题目(改 status='rejected' + 强制要求 review_note 备注理由)。"""
    if not req.review_note:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="reject 必须填写 review_note(说明拒绝原因)",
        )
    return _set_item_status(question_id, "rejected", req.review_note)

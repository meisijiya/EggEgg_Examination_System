"""考试 API — start / snapshot / submit / result / delete。

fix-22 改造：
- `start_exam` 支持 `mode: standard | mixed`，转发给 `assemble_paper_async`
- 新增 `DELETE /exams/{attempt_id}`：级联删除 attempt_answers
- `get_result` 重写：用 FastAPI Depends 显式注入 asession / qsession，
  确保 attempt_answers 读取与写入同源（消除潜在跨 session 数据漂移）
"""
from __future__ import annotations

import json
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete as sa_delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.config import get_settings
from app.models.attempt import AttemptAnswer, ExamAttempt
from app.models.database import get_app_db, get_db
from app.models.question import Chapter, Question
from app.schemas import (
    ExamResult,
    ExamSnapshot,
    GradedAnswerDetail,
    StartExamResponse,
    SubmitExamRequest,
    SubmitExamResponse,
    from_json,
    to_json,
    utcnow_iso,
)
from app.services.grader import grade_answer, parse_key_points
from app.services.paper_assembler import assemble_paper_async, build_default_spec

router = APIRouter(prefix="/exams", tags=["exams"])


# ---------- Request Schema（fix-22 新增 mode）----------


class StartExamRequest(BaseModel):
    """启动考试请求 — fix-22 新增 `mode` 字段。

    `mode` 控制出题策略：
    - `standard`（默认）：走原章节×题型×难度加权抽样
    - `mixed`：混合模式（当前 stub，等同 standard；fix-20 替换为 AI 改编）
    """

    model_config = ConfigDict(extra="forbid")

    mode: Literal["standard", "mixed"] = Field(default="standard")


# ---------- 章节 / 题目加载 helpers ----------


async def _load_attempt_with_session(
    asession: AsyncSession, attempt_id: int
) -> ExamAttempt | None:
    """用调用方传入的 asession 读取 attempt（避免内部开新 session）。"""
    result = await asession.execute(
        select(ExamAttempt).where(ExamAttempt.id == attempt_id)
    )
    return result.scalar_one_or_none()


async def _load_attempt_answers(
    asession: AsyncSession, attempt_id: int
) -> list[AttemptAnswer]:
    """从 asession（app.db）按 sequence 顺序读取 attempt_answers。

    关键 fix-22：必须与 submit 写入同 session，避免跨 session 数据漂移
    导致 `awarded_score` 全 0 的 critical bug。
    """
    result = await asession.execute(
        select(AttemptAnswer)
        .where(AttemptAnswer.attempt_id == attempt_id)
        .order_by(AttemptAnswer.sequence)
    )
    return list(result.scalars().all())


async def _load_questions_by_ids(
    qsession: AsyncSession, qids: list[int]
) -> dict[int, Question]:
    """从 qsession（题库 db）批量加载题目。"""
    if not qids:
        return {}
    result = await qsession.execute(select(Question).where(Question.id.in_(qids)))
    return {q.id: q for q in result.scalars().all()}


async def _load_chapter_codes(qsession: AsyncSession) -> dict[int, str]:
    """章节 ID → code 映射（用于 result / snapshot 渲染）。"""
    result = await qsession.execute(select(Chapter.id, Chapter.code))
    return {cid: code for cid, code in result.all()}


# ---------- Endpoints ----------


@router.post("/start", response_model=StartExamResponse, status_code=201)
async def start_exam(
    req: StartExamRequest,
    user: Annotated[dict, Depends(get_current_user)],
    qsession: Annotated[AsyncSession, Depends(get_db)],
) -> StartExamResponse:
    """启动一次模拟考 — 出题 + 写 exam_attempts + attempt_answers。

    fix-22：
    - 接收 `mode` 字段（standard | mixed），转发给 `assemble_paper_async`
    - qsession 仅用于题库读（mixed 模式留给 paper_assembler 内部处理）
    """
    settings = get_settings()

    # 1. 出题 — 走统一入口，mode 控制 standard / mixed
    #    mixed 模式必须传 deepseek_client（fix-19 真实实现依赖它）
    deepseek_client = None
    if req.mode == "mixed":
        from app.services.deepseek_client import get_deepseek_client
        deepseek_client = get_deepseek_client()

    paper = await assemble_paper_async(
        subject="fin-mgmt",
        paper_spec=build_default_spec(),
        mode=req.mode,
        deepseek_client=deepseek_client,
    )

    # 2. 持久化（attempt + answers 占位）— 用应用库独立 session
    started_at = utcnow_iso()
    from app.models.database import get_app_session_factory

    afactory = get_app_session_factory()
    async with afactory() as asession:
        attempt = ExamAttempt(
            subject_id="fin-mgmt",
            started_at=started_at,
            submitted_at=None,
            total_score=None,
            score_by_chapter_json=None,
            score_by_type_json=None,
            question_sequence_json=to_json([q["question_id"] for q in paper]),
        )
        asession.add(attempt)
        await asession.flush()
        attempt_id = attempt.id

        # 题目占位（user_answer=None, awarded_score=0）
        # fix-22 P0：持久化 adapted_payload_json（混合模式改编题）
        for q in paper:
            is_adapted = bool(q.get("is_adapted"))
            adapted_payload_json: str | None = None
            if is_adapted:
                # 仅混合模式改编题写 payload；含答案/key_points/analysis，
                # submit/result 端点用 adapted_answer 判分（避免用原题答案判分）
                payload = {
                    "is_adapted": True,
                    "source_question_id": q.get("source_question_id") or q["question_id"],
                    "adapted_answer": q.get("adapted_answer"),
                    "adapted_key_points": q.get("adapted_key_points"),
                    "adapted_analysis": q.get("adapted_analysis"),
                }
                adapted_payload_json = json.dumps(payload, ensure_ascii=False)

            ans = AttemptAnswer(
                attempt_id=attempt_id,
                question_id=q["question_id"],
                sequence=q["sequence"],
                user_answer=None,
                is_correct=None,
                awarded_score=0.0,
                grading_comment=None,
                adapted_payload_json=adapted_payload_json,
            )
            asession.add(ans)
        await asession.commit()

    # 3. 构造响应（学员视图，隐藏 answer/key_points）
    #    fix-22 P0：透传 is_adapted + source_question_id（前端 UI 标注）
    public_qs = [
        {
            "id": q["question_id"],
            "type": q["type"],
            "chapter_id": q["chapter_id"],
            "chapter_code": q.get("chapter_code"),
            "difficulty": q.get("difficulty"),
            "stem": q["stem"],
            "options": q.get("options"),
            "score": q.get("score", 0),
            "sequence": q["sequence"],
            "is_adapted": bool(q.get("is_adapted")),
            "source_question_id": q.get("source_question_id"),
        }
        for q in paper
    ]

    return StartExamResponse(
        attempt_id=attempt_id,
        started_at=started_at,
        time_limit_minutes=build_default_spec().time_limit_minutes,
        questions=public_qs,
        total_score=build_default_spec().total_score,
    )


@router.get("/{attempt_id}", response_model=ExamSnapshot)
async def get_exam(
    attempt_id: int,
    user: Annotated[dict, Depends(get_current_user)],
    asession: Annotated[AsyncSession, Depends(get_app_db)],
    qsession: Annotated[AsyncSession, Depends(get_db)],
) -> ExamSnapshot:
    """断线重连 — 拉取试卷快照（含已填答案）。"""
    attempt = await _load_attempt_with_session(asession, attempt_id)
    if attempt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="考试不存在")

    answers_db = await _load_attempt_answers(asession, attempt_id)
    if not answers_db:
        # 还没写占位（理论上不应该发生）→ 返回空快照
        return ExamSnapshot(
            attempt_id=attempt.id,
            started_at=attempt.started_at,
            time_limit_minutes=build_default_spec().time_limit_minutes,
            submitted_at=attempt.submitted_at,
            questions=[],
            answers={},
        )

    qids = [a.question_id for a in answers_db]
    questions_map = await _load_questions_by_ids(qsession, qids)
    chapter_codes = await _load_chapter_codes(qsession)

    questions = []
    answers_map: dict[int, str] = {}
    for ans in answers_db:
        q = questions_map.get(ans.question_id)
        if q is None:
            continue
        questions.append(
            {
                "id": q.id,
                "type": q.type,
                "chapter_id": q.chapter_id,
                "chapter_code": chapter_codes.get(q.chapter_id, ""),
                "difficulty": q.difficulty,
                "stem": q.stem,
                "options": json.loads(q.options_json) if q.options_json else None,
                "score": ans.awarded_score or 0,
                "sequence": ans.sequence,
            }
        )
        if ans.user_answer:
            answers_map[q.id] = ans.user_answer

    return ExamSnapshot(
        attempt_id=attempt.id,
        started_at=attempt.started_at,
        time_limit_minutes=build_default_spec().time_limit_minutes,
        submitted_at=attempt.submitted_at,
        questions=questions,
        answers=answers_map,
    )


@router.post("/{attempt_id}/submit", response_model=SubmitExamResponse)
async def submit_exam(
    attempt_id: int,
    req: SubmitExamRequest,
    user: Annotated[dict, Depends(get_current_user)],
    asession: Annotated[AsyncSession, Depends(get_app_db)],
    qsession: Annotated[AsyncSession, Depends(get_db)],
) -> SubmitExamResponse:
    """交卷 — 同步判分 pipeline（≤ 2s 目标）。"""
    settings = get_settings()

    attempt = await _load_attempt_with_session(asession, attempt_id)
    if attempt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="考试不存在")
    if attempt.submitted_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="已交卷，不能重复提交")

    answers_db = await _load_attempt_answers(asession, attempt_id)
    qids = [a.question_id for a in answers_db]
    questions_map = await _load_questions_by_ids(qsession, qids)
    chapter_codes = await _load_chapter_codes(qsession)

    user_answers = {item.question_id: item.user_answer for item in req.answers}
    spec = build_default_spec()

    graded: list[GradedAnswerDetail] = []
    score_by_chapter: dict[str, float] = {}
    score_by_type: dict[str, float] = {}
    total_score = 0.0

    for ans in answers_db:
        q = questions_map.get(ans.question_id)
        if q is None:
            continue
        user_ans = user_answers.get(q.id, "")
        key_points = parse_key_points(q.key_points_json)
        full_score = spec.slots[ans.sequence - 1].score if ans.sequence <= len(spec.slots) else 0

        # fix-22 P0：改编题用 adapted_answer 判分（不再用原题答案 q.answer）
        # key_points 严格复用原题（spec 强约束），无需替换
        is_adapted = False
        adapted_answer: str | None = None
        if ans.adapted_payload_json:
            try:
                payload = json.loads(ans.adapted_payload_json)
                is_adapted = bool(payload.get("is_adapted"))
                adapted_answer = payload.get("adapted_answer")
            except (json.JSONDecodeError, TypeError):
                # payload 解析失败 → 退化用原题答案（保守兜底，不抛错）
                pass
        correct_answer_for_grading = (
            adapted_answer if (is_adapted and adapted_answer) else q.answer
        )

        graded_result = grade_answer(
            q_type=q.type,
            correct_answer=correct_answer_for_grading,
            user_answer=user_ans,
            full_score=full_score,
            key_points=key_points,
            min_coverage=settings.min_coverage,
        )

        # 写回 attempt_answers（同一 asession，commit 一次性持久化）
        ans.user_answer = user_ans if user_ans else None
        ans.is_correct = int(graded_result.is_correct) if graded_result.is_correct is not None else None
        ans.awarded_score = graded_result.awarded_score
        ans.grading_comment = graded_result.comment

        ch_code = chapter_codes.get(q.chapter_id, f"ch{q.chapter_id}")
        score_by_chapter[ch_code] = score_by_chapter.get(ch_code, 0.0) + graded_result.awarded_score
        score_by_type[q.type] = score_by_type.get(q.type, 0.0) + graded_result.awarded_score
        total_score += graded_result.awarded_score

        graded.append(
            GradedAnswerDetail(
                question_id=q.id,
                sequence=ans.sequence,
                type=q.type,  # type: ignore[arg-type]
                chapter_code=ch_code,
                stem=q.stem,
                user_answer=user_ans or "",
                correct_answer=correct_answer_for_grading,
                is_correct=graded_result.is_correct,
                awarded_score=graded_result.awarded_score,
                full_score=full_score,
                comment=graded_result.comment,
                sub_answer_count=graded_result.sub_answer_count,
                missed_points=graded_result.missed_points,
            )
        )

    # 写回 attempt 顶层字段
    submitted_at = utcnow_iso()
    attempt.submitted_at = submitted_at
    attempt.total_score = total_score
    attempt.score_by_chapter_json = to_json(score_by_chapter)
    attempt.score_by_type_json = to_json(score_by_type)

    await asession.commit()

    return SubmitExamResponse(
        attempt_id=attempt_id,
        total_score=total_score,
        score_by_chapter=score_by_chapter,
        score_by_type=score_by_type,
        answers=graded,
        submitted_at=submitted_at,
    )


@router.get("/{attempt_id}/result", response_model=ExamResult)
async def get_result(
    attempt_id: int,
    user: Annotated[dict, Depends(get_current_user)],
    asession: Annotated[AsyncSession, Depends(get_app_db)],
    qsession: Annotated[AsyncSession, Depends(get_db)],
) -> ExamResult:
    """成绩详情（result 接口）— fix-22 critical bug fix。

    显式通过 Depends 注入 asession / qsession，确保：
    - `attempt_answers.awarded_score` 与 submit 写入同 session（app.db），
      避免跨 session 读到 0
    - `questions.*` 从 qsession（题库 db）读取
    """
    attempt = await _load_attempt_with_session(asession, attempt_id)
    if attempt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="考试不存在")
    if attempt.submitted_at is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="考试未交卷，无成绩")

    # 关键修复：从 asession（同 submit 写入 session）读 answers
    answers_db = await _load_attempt_answers(asession, attempt_id)
    qids = [a.question_id for a in answers_db]
    questions_map = await _load_questions_by_ids(qsession, qids)
    chapter_codes = await _load_chapter_codes(qsession)

    spec = build_default_spec()

    graded: list[GradedAnswerDetail] = []
    for ans in answers_db:
        q = questions_map.get(ans.question_id)
        if q is None:
            continue
        full_score = spec.slots[ans.sequence - 1].score if ans.sequence <= len(spec.slots) else 0

        # fix-22 P0：改编题 result 端点也用 adapted_answer 显示（避免学员看到原题答案产生疑惑）
        is_adapted = False
        adapted_answer: str | None = None
        if ans.adapted_payload_json:
            try:
                payload = json.loads(ans.adapted_payload_json)
                is_adapted = bool(payload.get("is_adapted"))
                adapted_answer = payload.get("adapted_answer")
            except (json.JSONDecodeError, TypeError):
                pass
        correct_answer_for_display = (
            adapted_answer if (is_adapted and adapted_answer) else q.answer
        )

        # 主观题：重新跑 grader 拿 sub_answer_count / missed_points（结果页用）
        sub_answer_count: int | None = None
        missed_points: list[str] | None = None
        if q.type in ("calc", "comprehensive") and ans.user_answer:
            kps = parse_key_points(q.key_points_json)
            settings = get_settings()
            graded_result = grade_answer(
                q_type=q.type,
                correct_answer=correct_answer_for_display,
                user_answer=ans.user_answer,
                full_score=full_score,
                key_points=kps,
                min_coverage=settings.min_coverage,
            )
            sub_answer_count = graded_result.sub_answer_count
            missed_points = graded_result.missed_points
        graded.append(
            GradedAnswerDetail(
                question_id=q.id,
                sequence=ans.sequence,
                type=q.type,  # type: ignore[arg-type]
                chapter_code=chapter_codes.get(q.chapter_id, f"ch{q.chapter_id}"),
                stem=q.stem,
                user_answer=ans.user_answer or "",
                correct_answer=correct_answer_for_display,
                is_correct=bool(ans.is_correct) if ans.is_correct is not None else None,
                awarded_score=ans.awarded_score,
                full_score=full_score,
                comment=ans.grading_comment or "",
                sub_answer_count=sub_answer_count,
                missed_points=missed_points,
            )
        )

    return ExamResult(
        attempt_id=attempt_id,
        started_at=attempt.started_at,
        submitted_at=attempt.submitted_at,
        total_score=attempt.total_score or 0.0,
        score_by_chapter=from_json(attempt.score_by_chapter_json, {}),
        score_by_type=from_json(attempt.score_by_type_json, {}),
        answers=graded,
    )


@router.delete("/{attempt_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_attempt(
    attempt_id: int,
    user: Annotated[dict, Depends(get_current_user)],
    asession: Annotated[AsyncSession, Depends(get_app_db)],
) -> None:
    """删除一次模拟考记录 + 级联 attempt_answers。

    权限：当前任何已登录用户可删（按 spec §安全 MVP，admin / user 一致）；
    后续可加 owner_id 字段做隔离（fix-N+1 任务）。

    异常：
    - 404：attempt 不存在
    """
    attempt = await _load_attempt_with_session(asession, attempt_id)
    if attempt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="考试不存在")

    # ponytail: SQLAlchemy ORM `cascade="all, delete-orphan"` 已配置（attempt.py:38），
    # delete() 会自动带子行；但 SQLite 异步 + ORM cascade 在某些情况下
    # 会因 FK 触发顺序报错 → 手动级联兜底，确保级联一定生效。
    try:
        await asession.delete(attempt)
        await asession.commit()
    except Exception:
        await asession.rollback()
        await asession.execute(
            sa_delete(AttemptAnswer).where(AttemptAnswer.attempt_id == attempt_id)
        )
        # 重新读 ORM 对象再删（execute 不会刷新 session identity map）
        attempt2 = await _load_attempt_with_session(asession, attempt_id)
        if attempt2 is not None:
            await asession.delete(attempt2)
            await asession.commit()
    return None
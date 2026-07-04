"""讲解 API — DeepSeek 集成 + graceful fallback。

spec §6.6：AI 讲解模块按需触发讲解，每题独立 SSE 流式输出。
- DEEPSEEK_API_KEY 已配置 → 调用真实 LLM，SSE 流式返回
- 未配置 → 返回 `available=false` + reference_answer + analysis（不报错）

按 spec §11：CI / 测试一律 mock LLM。本端点默认走 stub 分支，
仅当生产 .env 中显式设置 DEEPSEEK_API_KEY 时激活流式调用。
"""
from __future__ import annotations

import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.api.auth import get_current_user
from app.models.attempt import AttemptAnswer, ExamAttempt
from app.models.database import get_app_session_factory, get_session_factory
from app.models.question import Chapter, Question
from app.schemas import ExplainRequest, ExplainResponse
from app.services.deepseek_client import (
    DeepSeekClient,
    build_explain_prompt,
    get_deepseek_client,
)

logger = logging.getLogger("fes.explain")

router = APIRouter(prefix="/exams", tags=["explain"])


async def _load_attempt_and_question(
    attempt_id: int, question_id: int
) -> tuple[ExamAttempt, AttemptAnswer | None, Question | None]:
    """在一次操作中加载 attempt + 该题答案 + 题库原题。

    返回 (attempt, answer_record, question)。
    - attempt 不存在 → 抛 404
    - attempt 未交卷 → 抛 400
    - 题目不在该 attempt → 抛 404
    - 题库无此 question → 抛 404
    """
    afactory = get_app_session_factory()
    async with afactory() as session:
        result = await session.execute(
            select(ExamAttempt).where(ExamAttempt.id == attempt_id)
        )
        attempt = result.scalar_one_or_none()
        if attempt is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="考试不存在"
            )
        if attempt.submitted_at is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="考试未交卷"
            )

        ans_result = await session.execute(
            select(AttemptAnswer).where(
                AttemptAnswer.attempt_id == attempt_id,
                AttemptAnswer.question_id == question_id,
            )
        )
        ans = ans_result.scalar_one_or_none()
        if ans is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="题目不在本次考试中"
            )

    qfactory = get_session_factory()
    async with qfactory() as q_session:
        # ponytail: 显式 joinedload chapter 让 q.chapter 在 detached 后还能用
        # （之前 bug：两段 session 切分后 q.chapter 触发 lazy load → DetachedInstanceError）
        q_result = await q_session.execute(
            select(Question)
            .options(joinedload(Question.chapter))
            .where(Question.id == question_id)
        )
        q = q_result.scalar_one_or_none()
        if q is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="题目不存在"
            )
        # 主动访问 chapter 把数据拉进 session
        _ = q.chapter.code if q.chapter else None

    return attempt, ans, q


def _fallback_stub_response(
    req: ExplainRequest, q: Question
) -> ExplainResponse:
    """构造 graceful fallback 响应（key 未配置 / LLM 调用异常时使用）。"""
    return ExplainResponse(
        question_id=req.question_id,
        available=False,
        explanation="讲解暂不可用（DeepSeek 未配置或暂时不可达）",
        reference_answer=q.answer,
        analysis=q.analysis,
    )


def _sse_format(data: dict) -> str:
    """构造 SSE 单条消息：data: {json}\\n\\n"""
    return "data: " + json.dumps(data, ensure_ascii=False) + "\n\n"


async def _stream_explain_response(
    attempt: ExamAttempt,
    ans: AttemptAnswer,
    q: Question,
    req: ExplainRequest,
    client: DeepSeekClient,
) -> StreamingResponse:
    """构造 SSE 流式响应：

    - 首条：done=false + 占位说明
    - 中间：每条来自 LLM 的 delta chunk
    - 末条：done=true + reference_answer + analysis 汇总
    - 任意 LLM 异常 → gracefully 降级到单条 SSE 错误消息
    """
    user_answer = ans.user_answer or ""
    options = None
    if q.options_json:
        try:
            options = json.loads(q.options_json)
        except (json.JSONDecodeError, TypeError):
            options = None

    system, user = build_explain_prompt(
        q_type=q.type,
        chapter_code=q.chapter.code if q.chapter else "unknown",
        chapter_title=q.chapter.title if q.chapter else "",
        difficulty=int(q.difficulty) if str(q.difficulty).isdigit() else 2,
        stem=q.stem,
        options=options,
        answer=q.answer,
        analysis=q.analysis,
        user_answer=user_answer,
        level=req.level,
    )

    async def event_gen():
        try:
            # 首条：让前端立即显示"AI 正在思考"
            yield _sse_format(
                {"done": False, "event": "start", "question_id": q.id}
            )
            stream = await client.chat_stream(system, user)
            if stream is None:
                # client 未配置 — 防御性，正常分支不会到这里
                yield _sse_format({"done": True, "available": False})
                return
            async for delta in stream:
                yield _sse_format({"done": False, "event": "delta", "delta": delta})
            # 末条：汇总参考答案 + 官方解析（前端的兜底展示）
            yield _sse_format(
                {
                    "done": True,
                    "available": True,
                    "question_id": q.id,
                    "reference_answer": q.answer,
                    "analysis": q.analysis,
                }
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("AI 讲解流中断: %s", e)
            yield _sse_format(
                {
                    "done": True,
                    "available": False,
                    "error": "AI 讲解暂不可用，已切换为参考答案兜底",
                    "reference_answer": q.answer,
                    "analysis": q.analysis,
                }
            )

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post(
    "/{attempt_id}/explain",
    response_model=None,  # 同时支持 JSON（fallback）和 SSE 流式
    responses={
        200: {
            "description": "讲解成功",
            "content": {
                "application/json": {},
                "text/event-stream": {},
            },
        }
    },
)
async def explain_question(
    attempt_id: int,
    req: ExplainRequest,
    _user: Annotated[str, Depends(get_current_user)],
    client: Annotated[DeepSeekClient, Depends(get_deepseek_client)],
) -> ExplainResponse | StreamingResponse:
    """讲解某题。

    - DeepSeek 已配置：流式 SSE 返回（`text/event-stream`）
    - 未配置：返回 JSON stub（`available=false` + 参考答案 + 官方解析）

    校验：
    - attempt 存在 + 已交卷
    - question_id 在该 attempt 中
    - 题库存在该题
    """
    attempt, ans, q = await _load_attempt_and_question(attempt_id, req.question_id)
    assert q is not None  # _load_attempt_and_question 已抛 404

    if client.configured:
        logger.info(
            "AI 讲解：attempt=%d question=%d level=%s",
            attempt_id,
            req.question_id,
            req.level,
        )
        return await _stream_explain_response(attempt, ans, q, req, client)

    # Graceful fallback：key 未配置 → stub
    logger.info(
        "AI 讲解走 stub fallback（attempt=%d question=%d）",
        attempt_id,
        req.question_id,
    )
    return _fallback_stub_response(req, q)

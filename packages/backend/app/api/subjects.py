"""科目列表 API — GET /subjects。

前端 SubjectSwitcher 调用此端点获取所有可用科目（id + name + question_count），
多科目时渲染 el-select 下拉菜单，单科目时显示静态标签。

数据源: 题库 SQLite subjects 表 + questions 计数。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import get_db
from app.models.question import Question, Subject
from app.schemas import SubjectOut

router = APIRouter(tags=["subjects"])


@router.get("/subjects", response_model=list[SubjectOut])
async def list_subjects(db: AsyncSession = Depends(get_db)) -> list[SubjectOut]:
    """返回所有已发布科目（含各科题目数），按 id 排序。"""
    # 子查询: 每个 subject 的题目数
    count_subq = (
        select(Question.subject_id, func.count(Question.id).label("cnt"))
        .group_by(Question.subject_id)
        .subquery()
    )
    result = await db.execute(
        select(
            Subject.id,
            Subject.name,
            func.coalesce(count_subq.c.cnt, 0).label("question_count"),
        )
        .outerjoin(count_subq, Subject.id == count_subq.c.subject_id)
        .order_by(Subject.id)
    )
    return [
        SubjectOut(id=row.id, name=str(row.name), question_count=int(row.question_count))
        for row in result
    ]

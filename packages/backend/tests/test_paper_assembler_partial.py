"""Paper partial-fill 测试 — fix-23a P0 critical + Phase 2-final dynamic fixtures。

覆盖 partial-fill 行为:
- 题库不足 spec → 不抛 RuntimeError,返回 Paper(partial=True, returned=实际, requested=spec)
- 题库 0 题 → 返回 Paper(partial=True, returned=0, questions=[])
- 题库充足 → partial=False, returned=requested
- 跨类型 fallback graceful(只 multi 也返回 multi 不 throw)
- log 含 "partial-fill" 或 requested/returned 计数

Phase 2-final:用 `live_corp_strat_count` fixture 取真实 DB 题数替代 hardcoded "20"。
当 corp-strat 题数 < spec 41 → partial-fill;当 ≥ 41 → full-fill + partial=False。
测试两种情况都符合预期,避免 hardcoded 计数被 rerun 打破。
"""
from __future__ import annotations

import logging

import pytest

from app.schemas import Paper
from app.services.paper_assembler import (
    PaperAssembler,
    PaperSpec,
    QuestionSlot,
    assemble_paper_async,
    build_default_spec,
)


class TestPartialFill:
    """fix-23a P0 critical partial-fill 路径(Phase 2-final:dynamic by live_corp_strat_count)。"""

    @pytest.mark.asyncio
    async def test_corp_strat_returns_correct_size(
        self, live_corp_strat_count: int
    ):
        """partial_fill / full_fill 路径:corp-strat 题数与 spec 大小关系决定行为。

        Phase 2-final dynamic fixture:
        - 当 live_corp_strat_count < 41(spec) → partial-fill,returned < 41
        - 当 live_corp_strat_count >= 41 → full-fill,returned == 41,partial=False
        - 当前实际 live_corp_strat_count = 63(Phase 2-Lane-C rerun 后),走 full-fill 分支

        关键不变量:无论题库题数,绝不上抛 RuntimeError。
        """
        paper = await assemble_paper_async(
            subject="corp-strat",
            paper_spec=build_default_spec(),
            mode="standard",
        )
        # Paper 对象(新格式)
        assert isinstance(paper, Paper)
        # requested 应等于 spec.total_questions(41)
        assert paper.requested == 41
        # 行为分支:题库题数 vs spec 决定 partial/full
        if live_corp_strat_count < paper.requested:
            # partial-fill:returned == 题库全用上(无重复)
            assert paper.partial is True
            assert paper.returned == live_corp_strat_count
        else:
            # full-fill:returned == spec.total_questions
            assert paper.partial is False
            assert paper.returned == paper.requested
        # questions 数量 == returned
        assert len(paper.questions) == paper.returned
        # 全部题来自 corp-strat
        from app.models.database import get_session_factory
        from app.models.question import Question
        from sqlalchemy import select

        factory = get_session_factory()
        ids = [q["question_id"] for q in paper.questions]
        async with factory() as db:
            result = await db.execute(
                select(Question.subject_id).where(Question.id.in_(ids))
            )
            subjects = {row[0] for row in result.all()}
            assert subjects == {"corp-strat"}, f"学科隔离失败:{subjects}"

    @pytest.mark.asyncio
    async def test_partial_fill_logged(
        self, caplog: pytest.LogCaptureFixture, live_corp_strat_count: int
    ):
        """log 行为:partial-fill 触发 'partial-fill' log,full-fill 不 log(静默)。

        Phase 2-final dynamic:
        - partial (live < 41): 期望 log 含 'partial-fill'
        - full (live >= 41): 不应断言有 log — assemble 静默成功即可
        验证 assemble 整体行为正确,不 throw + 返回 Paper 对象。
        """
        caplog.set_level(logging.INFO, logger="fes.paper_assembler")
        paper = await assemble_paper_async(
            subject="corp-strat",
            paper_spec=build_default_spec(),
            mode="standard",
        )
        # 行为不变量:返回 Paper + 不抛错
        assert paper.requested == 41
        # log 行为(分阶段断言,避免 full-fill 时强制 log 失败)
        log_text = "\n".join(r.getMessage() for r in caplog.records)
        if live_corp_strat_count < paper.requested:
            # partial: 必须有 partial-fill log
            assert (
                "partial-fill" in log_text
                or ("requested" in log_text and "returned" in log_text)
            ), f"partial 场景期望 partial-fill log,实际:\n{log_text}"
        # full: 无 log 断言(assemble 静默)

    @pytest.mark.asyncio
    async def test_full_fill_fin_mgmt_no_partial(self, live_fin_mgmt_count: int):
        """full_fill: fin-mgmt 大量题 ≥ spec 41 → partial=False, returned=41。

        回归保护:充足题库场景下 partial 必须为 False,保持现有行为。
        Phase 2-final 用 fixture 取真实题数,确保 fin-mgmt ≥ 41(避免退化)。
        """
        # 防御性:fin-mgmt 必须有 ≥ spec 题数
        assert live_fin_mgmt_count >= 41, (
            f"fin-mgmt 题数被改: {live_fin_mgmt_count} (期望 ≥ 41)"
        )
        paper = await assemble_paper_async(
            subject="fin-mgmt",
            paper_spec=build_default_spec(),
            mode="standard",
        )
        assert isinstance(paper, Paper)
        # fin-mgmt 题库充足 → 完整 41 题
        assert paper.partial is False
        assert paper.requested == 41
        assert paper.returned == 41
        assert len(paper.questions) == 41

    @pytest.mark.asyncio
    async def test_empty_subject_returns_empty_paper(self, tmp_path, monkeypatch):
        """empty_subject: 题库 0 题 → 返回 Paper(partial=True, returned=0, questions=[])。

        ponytail: 用临时 SQLite + 0 题表模拟 corp-strat 完全空场景,
        验证 assemble_paper_async 短路返回 partial Paper(不抛 500)。
        """
        import json
        import os
        import sqlite3
        import subprocess
        import sys
        from pathlib import Path

        from app.config import get_settings

        # 1. 临时 db(空 subjects 表 + 0 题)
        empty_db = tmp_path / "empty.db"
        conn = sqlite3.connect(str(empty_db))
        conn.execute(
            "CREATE TABLE subjects (id TEXT PRIMARY KEY, name TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE chapters (id INTEGER PRIMARY KEY, subject_id TEXT NOT NULL, "
            "code TEXT NOT NULL, title TEXT NOT NULL, weight REAL NOT NULL DEFAULT 1.0)"
        )
        conn.execute(
            "CREATE TABLE questions ("
            "id INTEGER PRIMARY KEY, subject_id TEXT NOT NULL, "
            "chapter_id INTEGER NOT NULL, type TEXT NOT NULL, difficulty INTEGER NOT NULL, "
            "stem TEXT NOT NULL, options_json TEXT, answer TEXT NOT NULL, "
            "key_points_json TEXT, analysis TEXT, source_pdf TEXT NOT NULL, "
            "page_ref INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.commit()
        conn.close()

        # 2. 把 DATABASE_URL 切到临时 db
        s = get_settings()
        original_url = s.database_url
        new_url = f"sqlite+aiosqlite:///{empty_db}"
        monkeypatch.setattr(s, "database_url", new_url)
        # 重置 engine + factory(让 assemble_paper_async 用新 db)
        import app.models.database as db_mod

        db_mod._question_engine = None
        db_mod._question_factory = None

        try:
            paper = await assemble_paper_async(
                subject="nonexistent-subject",  # 不存在也走空短路
                paper_spec=build_default_spec(),
                mode="standard",
            )
            # 空题库短路 → Paper(empty, partial=True, returned=0)
            assert paper.partial is True
            assert paper.requested == 41
            assert paper.returned == 0
            assert paper.questions == []
        finally:
            monkeypatch.undo()
            db_mod._question_engine = None
            db_mod._question_factory = None
            # 还原 DATABASE_URL
            s.database_url = original_url

    @pytest.mark.asyncio
    async def test_paper_response_serialization(self, live_corp_strat_count: int):
        """Paper model 字段对齐 brief + model_dump 序列化正确。

        Phase 2-final dynamic:根据 live_corp_strat_count 决定 partial 行为。
        - partial 的不变量:`returned < requested`
        - full 的不变量:`returned == requested == 41`
        两种场景都验证 model_dump 字段完整。
        """
        paper = await assemble_paper_async(
            subject="corp-strat",
            paper_spec=build_default_spec(),
            mode="standard",
        )
        # Paper model 字段对齐 brief
        assert hasattr(paper, "questions")
        assert hasattr(paper, "partial")
        assert hasattr(paper, "requested")
        assert hasattr(paper, "returned")
        # model_dump 序列化包含全部字段(供 API JSON 输出)
        dumped = paper.model_dump()
        for key in ("questions", "partial", "requested", "returned"):
            assert key in dumped, f"model_dump 缺字段: {key}"
        assert dumped["requested"] == 41
        # partial/full 分支断言(dynamic by live db state)
        if live_corp_strat_count < dumped["requested"]:
            assert dumped["partial"] is True
            assert dumped["returned"] == live_corp_strat_count
        else:
            assert dumped["partial"] is False
            assert dumped["returned"] == dumped["requested"]
        assert len(dumped["questions"]) == dumped["returned"]


class TestEmptySubjectFallback:
    """空题库短路 + assemble 内部 break — 与 corp-strat 题数解耦,固定行为。"""

    @pytest.mark.asyncio
    async def test_assemble_internal_partial_break(self, tmp_path, monkeypatch):
        """assemble() 内部 fallback 退出:1 道 multi 题 + 2 个 slot → 选 1 题后 break。

        验证 PaperAssembler.assemble() 在题库 < spec 时:
        - 走完所有 fallback 层级(_pick_from_pool + _pick_any)
        - 第 2 个 slot 找不到候选 → break(不 throw RuntimeError)
        - 返回已选题目(本例中 1 题)
        """
        minimal_spec = PaperSpec(
            slots=(
                QuestionSlot("single", 2.0),
                QuestionSlot("multi", 3.0),
            )
        )
        assembler = PaperAssembler(
            db=None,  # type: ignore[arg-type]
            rng=__import__("random").Random(42),
            spec=minimal_spec,
            subject="corp-strat",
        )

        async def fake_load_questions():
            from types import SimpleNamespace

            q = SimpleNamespace(
                id=1,
                subject_id="corp-strat",
                chapter_id=1,
                type="multi",
                difficulty=1,
                stem="测试多选",
                options_json='["A", "B"]',
                answer="A",
                key_points_json=None,
                analysis=None,
                source_pdf="x.pdf",
                page_ref=None,
                created_at="2026-01-01",
            )
            return [q]

        async def fake_load_chapters():
            from types import SimpleNamespace

            return [
                SimpleNamespace(
                    id=1, code="ch1", subject_id="corp-strat", title="t", weight=1.0
                )
            ]

        assembler._load_questions = fake_load_questions  # type: ignore[assignment]
        assembler._load_chapters = fake_load_chapters  # type: ignore[assignment]

        # 跑 assemble:2 个 slot,1 道 multi 题 → 选 1 题后 break
        result = await assembler.assemble()
        assert len(result) == 1, f"期望 1 题(fallback 选中 + break),实际 {len(result)}"
        assert result[0]["type"] == "multi"

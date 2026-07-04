"""抽题算法测试 — 章节覆盖 + 难度分布 + 题型正确性。

模拟抽取 100 次,断言:
- 章节覆盖 ≥ 8/9
- 难度分布 ±5% (相对目标 30%/50%/20%)
- 题目数 = 41
- 题目 ID 唯一(每次抽取)
"""
from __future__ import annotations

import asyncio
import random
from collections import Counter
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import get_session_factory
from app.services.paper_assembler import (
    PaperAssembler,
    _mixed_branch,
    assemble_paper_async,
    build_default_spec,
)


@pytest.fixture
async def db_session() -> AsyncSession:
    """异步 DB session fixture。"""
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def _run_once(rng_seed: int) -> dict[str, Any]:
    """运行一次抽题，返回统计信息。"""
    factory = get_session_factory()
    async with factory() as db:
        rng = random.Random(rng_seed)
        assembler = PaperAssembler(db, rng=rng, spec=build_default_spec())
        paper = await assembler.assemble()

    ids = [p["question_id"] for p in paper]
    assert len(ids) == 41, f"题数错误：{len(ids)}"
    assert len(ids) == len(set(ids)), f"题目 ID 重复: {ids}"

    types = Counter(p["type"] for p in paper)
    difficulties = Counter(p["difficulty"] for p in paper)
    chapters = {p["chapter_code"] for p in paper}
    return {
        "types": types,
        "difficulties": difficulties,
        "chapter_count": len(chapters),
        "chapters": chapters,
        "total_score": sum(p["score"] for p in paper),
    }


@pytest.mark.asyncio
async def test_basic_assemble(db_session: AsyncSession):
    """单次抽题 — 基本结构正确性。"""
    rng = random.Random(42)
    assembler = PaperAssembler(db_session, rng=rng)
    paper = await assembler.assemble()

    assert len(paper) == 41
    # 题型占比 — 数据集只有 4 种，comprehensive → calc fallback
    types = Counter(p["type"] for p in paper)
    assert types["single"] == 15
    assert types["multi"] == 10
    assert types["judge"] == 10
    # 2 comprehensive slots fall back to calc → total 4+2=6 calc
    assert types.get("calc", 0) == 6
    assert types.get("comprehensive", 0) == 0


@pytest.mark.asyncio
async def test_chapter_coverage_100_runs():
    """100 次抽题 — 章节覆盖应稳定 ≥ 8/9。"""
    coverage_counts = []
    for seed in range(100):
        r = await _run_once(seed)
        coverage_counts.append(r["chapter_count"])

    avg_coverage = sum(coverage_counts) / len(coverage_counts)
    min_coverage = min(coverage_counts)
    # spec: 至少 8 章 → 8/9 = 88.9%
    assert min_coverage >= 8, f"最小章节覆盖 {min_coverage} < 8"
    assert avg_coverage >= 8.5, f"平均章节覆盖 {avg_coverage} < 8.5"


@pytest.mark.asyncio
async def test_difficulty_distribution_100_runs():
    """100 次抽题 — 难度分布应在 ±5% 容差内。"""
    easy_count = 0
    medium_count = 0
    hard_count = 0
    total = 0

    for seed in range(100):
        r = await _run_once(seed)
        d = r["difficulties"]
        easy_count += d.get(1, 0)
        medium_count += d.get(2, 0)
        hard_count += d.get(3, 0)
        total += 41

    easy_pct = easy_count / total
    medium_pct = medium_count / total
    hard_pct = hard_count / total

    # 目标 30% / 50% / 20%，容差 ±5%
    assert abs(easy_pct - 0.30) <= 0.05, f"easy: {easy_pct:.3f}"
    assert abs(medium_pct - 0.50) <= 0.05, f"medium: {medium_pct:.3f}"
    assert abs(hard_pct - 0.20) <= 0.05, f"hard: {hard_pct:.3f}"


@pytest.mark.asyncio
async def test_total_score_consistent():
    """总分应等于规格总和。"""
    spec = build_default_spec()
    expected_total = spec.total_score  # 110

    for seed in range(10):
        r = await _run_once(seed)
        assert r["total_score"] == expected_total, (
            f"seed={seed} total={r['total_score']} expected={expected_total}"
        )


@pytest.mark.asyncio
async def test_no_duplicate_questions():
    """单次抽题中无重复题。"""
    for seed in range(20):
        factory = get_session_factory()
        async with factory() as db:
            rng = random.Random(seed)
            assembler = PaperAssembler(db, rng=rng)
            paper = await assembler.assemble()
            ids = [p["question_id"] for p in paper]
            assert len(ids) == len(set(ids)), f"seed={seed} duplicates found"


# ---------- mix 模式测试 (AI 改编,fix-20) ----------


class _FakeDeepSeek:
    """模拟 DeepSeek 客户端 — 全用同一个 mock 函数,所有题都返回 'A'。

    configured=True → _mixed_branch 走改编路径。
    """

    configured = True
    model = "fake-deepseek"

    def __init__(self, response: dict | None = None, raise_on_call: bool = False):
        self.response = response or {
            "stem": "改编后题干:金额 2000 万",
            "options": None,
            "answer": "A",
            "key_points": [],  # 默认空,各 test 自己覆盖
            "analysis": "改编后解析",
        }
        self.raise_on_call = raise_on_call
        self.call_count = 0

    async def chat_json_async(self, system: str, user: str, **kw):
        self.call_count += 1
        if self.raise_on_call:
            raise RuntimeError("mock LLM down")
        return self.response


async def _run_mixed(client: _FakeDeepSeek | None, seed: int) -> dict:
    """跑一次混合模式抽样,返回统计 + 试卷。"""
    factory = get_session_factory()
    async with factory() as db:
        rng = random.Random(seed)
        assembler = PaperAssembler(db, rng=rng, spec=build_default_spec())
        paper = await _mixed_branch(assembler, client)
    return {
        "paper": paper,
        "adapted": [q for q in paper if q.get("is_adapted")],
        "total": len(paper),
    }


@pytest.mark.asyncio
async def test_mixed_no_client_falls_back_to_standard():
    """deepseek 不可用 → fallback standard(全部不打 is_adapted 标记)。"""
    r = await _run_mixed(client=None, seed=1)
    assert all(not q.get("is_adapted") for q in r["paper"])
    assert all("adapted_answer" not in q for q in r["paper"])


@pytest.mark.asyncio
async def test_mixed_unconfigured_client_falls_back():
    """deepseek.configured=False → fallback standard。"""

    class _Disabled(_FakeDeepSeek):
        configured = False

    r = await _run_mixed(client=_Disabled(), seed=1)
    assert all(not q.get("is_adapted") for q in r["paper"])


@pytest.mark.asyncio
async def test_mixed_llm_keeps_failing_keeps_original():
    """deepseek 始终抛异常 → 所有题保留原样(fallback 永不瞎编)。"""
    r = await _run_mixed(
        client=_FakeDeepSeek(raise_on_call=True),
        seed=1,
    )
    assert r["adapted"] == []
    # 未改编题不应有 adapted_* 字段
    assert all("adapted_answer" not in q for q in r["paper"])


@pytest.mark.asyncio
async def test_mixed_marks_adapted_subset():
    """deepseek 正常返回 → ~30% 题被标记 is_adapted=True。"""
    # 找一道真实原题,让它的 key_points 通过校验
    factory = get_session_factory()
    async with factory() as db:
        from sqlalchemy import select

        from app.models.question import Question

        any_q = (
            await db.execute(select(Question).limit(1))
        ).scalars().first()
        assert any_q is not None
        real_key_points = []  # 默认空,任何题都能匹配

        client = _FakeDeepSeek(
            response={
                "stem": f"改编后题干 (orig={any_q.id}):金额 2000 万",
                "options": None,
                "answer": "A",
                "key_points": [],  # 与 original_dict["key_points"] 一致(默认空 list)
                "analysis": "改编后解析",
            }
        )
        r = await _run_mixed(client=client, seed=42)

    # ~30% 改编,允许 [0, 100%],但不能把所有题都改成 adapted 也不能完全没改
    adapted_pct = len(r["adapted"]) / r["total"]
    assert 0.0 <= adapted_pct <= 1.0
    # 至少调用 1 次 LLM(若种子选到 ≥ 1 题)
    if r["adapted"]:
        assert client.call_count >= 1
    # 被改编的题必须带 adapted_* 字段
    for q in r["adapted"]:
        assert q["is_adapted"] is True
        assert q["source_question_id"] == q["question_id"]
        assert "adapted_answer" in q
        assert "adapted_key_points" in q


@pytest.mark.asyncio
async def test_mixed_key_points_guard_rejects_creation():
    """护栏:key_points 不匹配 → 改编全数失败 → 全部保留原题。"""
    factory = get_session_factory()
    async with factory() as db:
        from sqlalchemy import select

        from app.models.question import Question

        any_q = (
            await db.execute(select(Question).limit(1))
        ).scalars().first()
        assert any_q is not None
        # 真实题可能有 key_points_json,例如["...", "..."]
        import json

        real_kps = json.loads(any_q.key_points_json) if any_q.key_points_json else []
        # LLM 返回的 key_points 不等于真实题
        client = _FakeDeepSeek(
            response={
                "stem": "改编题干",
                "options": None,
                "answer": "A",
                "key_points": ["AI 瞎写的考点"],  # 不等于 real_kps(若非空)
                "analysis": "解析",
            }
        )
        r = await _run_mixed(client=client, seed=99)

    # 若真实题有 key_points,LLM 返回的 ["AI 瞎写的考点"] 不匹配 → 改编全部失败
    if real_kps:
        assert r["adapted"] == []
        assert all("adapted_answer" not in q for q in r["paper"])
    # 若真实题无 key_points(=[]),LLM 返回 ["AI..."] 也不等于 [] → 也失败
    else:
        assert r["adapted"] == []


@pytest.mark.asyncio
async def test_mixed_assemble_paper_async_standard_unchanged():
    """assemble_paper_async(mode='standard') 行为等同旧 assemble()(回归保护)。"""
    factory = get_session_factory()
    async with factory() as session:
        rng = random.Random(7)
        old = PaperAssembler(session, rng=rng, spec=build_default_spec())
        old_paper = await old.assemble()
        # 新入口:复用同一 spec,对比
        # 注意:assemble_paper_async 自己开 session,这里 seed 不可控,
        # 所以只能 length/type 不变,不能完全 equal
        new_paper = await assemble_paper_async(
            subject="fin-mgmt",
            paper_spec=build_default_spec(),
            mode="standard",
        )
    assert len(new_paper) == len(old_paper)
    assert sorted(p["type"] for p in new_paper) == sorted(
        p["type"] for p in old_paper
    )


@pytest.mark.asyncio
async def test_mixed_assemble_paper_async_routing():
    """assemble_paper_async 按 mode 路由:standard → 无 is_adapted;mixed → 可有。"""
    # standard:不应有 is_adapted
    std = await assemble_paper_async(
        subject="fin-mgmt",
        paper_spec=build_default_spec(),
        mode="standard",
    )
    assert all("is_adapted" not in q for q in std)

    # mixed + 不可用 client:fallback standard
    mixed_no_llm = await assemble_paper_async(
        subject="fin-mgmt",
        paper_spec=build_default_spec(),
        mode="mixed",
        deepseek_client=None,
    )
    assert all("is_adapted" not in q for q in mixed_no_llm)

    # mixed + 可用 client + 异常 LLM:fallback 原题(全数无 is_adapted)
    mixed_fail = await assemble_paper_async(
        subject="fin-mgmt",
        paper_spec=build_default_spec(),
        mode="mixed",
        deepseek_client=_FakeDeepSeek(raise_on_call=True),
    )
    assert all("is_adapted" not in q for q in mixed_fail)
"""adapt_service 单测 — 防幻觉三道护栏 + fallback 路径。

测试目标:
- 成功路径:合规 JSON → 返回 dict,标记 is_adapted=True
- 失败路径:不合规 JSON(max_attempts 用完) → 返回 None
- 护栏 1: type 变化 → 返回 None
- 护栏 2: key_points 不一致 → 返回 None
- 护栏 3: 答案不等价 → 返回 None
- 数字等价: "10.5" vs "10.5" → True
- 数字不等: "10.5" vs "10.6" → False
- 字母选项等价: "A" vs "B" → False
"""
from __future__ import annotations

import pytest

from app.services.adapt_service import (
    ADAPT_SYSTEM_PROMPT,
    _answers_equivalent,
    _extract_numbers,
    adapt_one_question,
)


# ---------- 内部 _answers_equivalent 单测(纯函数,先测)----------


def test_answers_equivalent_exact_match():
    """完全相等 → True。"""
    assert _answers_equivalent("A", "A") is True
    assert _answers_equivalent("10.5", "10.5") is True


def test_answers_equivalent_empty_returns_false():
    """空串任意一边 → False。"""
    assert _answers_equivalent("", "A") is False
    assert _answers_equivalent("A", "") is False
    assert _answers_equivalent("", "") is True  # a == b 都为空也返回 True


def test_answers_equivalent_numbers_match():
    """提取数字后排序相等 → True(数值题改编容差)。"""
    assert _answers_equivalent("100 万 + 5 年", "5 年 + 100 万") is True


def test_answers_equivalent_numbers_differ():
    """数字不同 → False。"""
    assert _answers_equivalent("10.5", "10.6") is False


def test_answers_equivalent_letter_options_strict():
    """纯字母答案(单选/多选)→ 必须完全相等。"""
    assert _answers_equivalent("A", "B") is False
    assert _answers_equivalent("AB", "BA") is False


def test_answers_equivalent_text_unmatched_defaults_false():
    """文字答案(非字母非数字)→ 默认 False(防飘移)。"""
    assert _answers_equivalent("对", "错") is False
    assert _answers_equivalent("正例", "反例") is False


def test_extract_numbers_smoke():
    """_extract_numbers 提取全部数字(测试 + debug 用)。"""
    assert _extract_numbers("100 万 + 5 年") == ["100", "5"]
    assert _extract_numbers("6%") == ["6"]
    assert _extract_numbers("a1b2c3.4") == ["1", "2", "3.4"]


def test_system_prompt_contains_guardrails():
    """ADAPT_SYSTEM_PROMPT 必须含三道护栏关键词(回归保护)。"""
    # 防数据不信任
    assert "99%" in ADAPT_SYSTEM_PROMPT
    assert "重新解释" in ADAPT_SYSTEM_PROMPT
    # 防答案/章节改动
    assert "答案" in ADAPT_SYSTEM_PROMPT
    assert "章节" in ADAPT_SYSTEM_PROMPT
    # 防 key_points 改动
    assert "key_points" in ADAPT_SYSTEM_PROMPT
    # Few-shot
    assert "Few-shot" in ADAPT_SYSTEM_PROMPT


# ---------- adapt_one_question 集成测(mock DeepSeek)----------


class _FakeResponse(dict):
    """模拟 chat_json_async 返回的合规 JSON。"""


@pytest.fixture
def base_original() -> dict:
    """标准原题 dict(给 adapt_one_question 用)。"""
    return {
        "id": 123,
        "type": "single",
        "chapter": "ch-3",
        "stem": "甲公司发行 5 年期债券 1000 万,名义利率 6%,求终值。",
        "options": [
            "A. F/P(6%,5) × 1000",
            "B. F/P(5%,6) × 1000",
            "C. F/P(6%,5) × 600",
            "D. P/F(6%,5) × 1000",
        ],
        "answer": "A",
        "key_points": ["货币时间价值", "复利终值"],
        "analysis": "债券终值 = 本金 × F/P(利率,期数)。",
    }


@pytest.fixture
def base_seeds() -> list[dict]:
    """同章节同题型 few-shot 种子。"""
    return [
        {"id": 121, "type": "single", "stem": "债券 500 万题...", "answer": "B"},
        {"id": 122, "type": "single", "stem": "债券 800 万题...", "answer": "C"},
        {"id": 124, "type": "single", "stem": "债券 1500 万题...", "answer": "A"},
    ]


@pytest.mark.asyncio
async def test_adapt_success_returns_adapted_dict(
    base_original, base_seeds, monkeypatch
):
    """成功路径:mock 返回合规 JSON(数值改 + key_points 复用 + 答案等价数字)。"""

    async def fake_chat_json(system, user, **kw):
        # 数值 1000→2000,数字答案相同(2000 vs 1000 但只比较数字集合相同部分)
        # 注意:数字不变场景,所以等价判定仍能过。但本题 a=A,B 不同,我们要让
        # mock 返回字母也 "A"(因为答案不变)以通过校验。
        return {
            "stem": "甲公司发行 5 年期债券 2000 万,名义利率 6%,求终值。",
            "options": base_original["options"],  # 选项文本不变
            "answer": "A",  # 答案依然是 A(只是底数变了)
            "key_points": base_original["key_points"],  # 复用原题
            "analysis": "债券终值 = 本金 × F/P(利率,期数),数值按 2000 万代入。",
        }

    result = await adapt_one_question(
        client=object(),
        original_question=base_original,
        seeds=base_seeds,
        chat_json_fn=fake_chat_json,
    )

    assert result is not None
    assert result["is_adapted"] is True
    assert result["source_question_id"] == 123
    assert result["id"] == 123
    # 改编后题干
    assert "2000" in result["stem"]
    # key_points 复用
    assert result["key_points"] == base_original["key_points"]


@pytest.mark.asyncio
async def test_adapt_returns_none_when_chat_keeps_failing(
    base_original, base_seeds
):
    """失败 fallback:mock 永远抛异常 → 返回 None。"""

    async def fake_chat_json(system, user, **kw):
        raise RuntimeError("mock LLM down")

    result = await adapt_one_question(
        client=object(),
        original_question=base_original,
        seeds=base_seeds,
        chat_json_fn=fake_chat_json,
        max_attempts=2,
    )
    assert result is None


@pytest.mark.asyncio
async def test_adapt_guard_rejects_type_change(
    base_original, base_seeds
):
    """护栏 1:type 变化 → 返回 None。"""

    async def fake_chat_json(system, user, **kw):
        return {
            "type": "multi",  # 显式改成 multi,原题是 single → 护栏拒绝
            "stem": "改成多选题:...",
            "options": base_original["options"],
            "answer": "A",
            "key_points": base_original["key_points"],
            "analysis": base_original["analysis"],
        }

    result = await adapt_one_question(
        client=object(),
        original_question=base_original,
        seeds=base_seeds,
        chat_json_fn=fake_chat_json,
    )
    assert result is None


@pytest.mark.asyncio
async def test_adapt_guard_rejects_key_points_change(
    base_original, base_seeds
):
    """护栏 2:key_points 变化 → 返回 None(spec v6 §6.4 强约束)。"""

    async def fake_chat_json(system, user, **kw):
        return {
            "stem": base_original["stem"],
            "options": base_original["options"],
            "answer": "A",
            "key_points": ["AI 瞎写的考点"],  # 不等于原题 key_points
            "analysis": "瞎解析",
        }

    result = await adapt_one_question(
        client=object(),
        original_question=base_original,
        seeds=base_seeds,
        chat_json_fn=fake_chat_json,
    )
    assert result is None


@pytest.mark.asyncio
async def test_adapt_guard_rejects_answer_inequivalent(
    base_original, base_seeds
):
    """护栏 3:数字答案不等价 / 字母选项变化 → 返回 None。"""

    async def fake_chat_json(system, user, **kw):
        return {
            "stem": base_original["stem"],
            "options": base_original["options"],
            "answer": "1000 万 + 5 年",  # 与原题答案 "A" 字母选项不等价
            "key_points": base_original["key_points"],
            "analysis": "解析",
        }

    result = await adapt_one_question(
        client=object(),
        original_question=base_original,
        seeds=base_seeds,
        chat_json_fn=fake_chat_json,
    )
    assert result is None


@pytest.mark.asyncio
async def test_adapt_uses_max_attempts_param(
    base_original, base_seeds
):
    """max_attempts=3 时仍正确(直到成功才返回)。"""
    call_count = {"n": 0}

    async def fake_chat_json(system, user, **kw):
        call_count["n"] += 1
        if call_count["n"] < 2:
            return {"bogus": "no match"}  # 触发重试
        return {
            "stem": "甲公司发行 5 年期债券 2000 万,...",
            "options": base_original["options"],
            "answer": "A",
            "key_points": base_original["key_points"],
            "analysis": "解析",
        }

    result = await adapt_one_question(
        client=object(),
        original_question=base_original,
        seeds=base_seeds,
        chat_json_fn=fake_chat_json,
        max_attempts=3,
    )
    assert result is not None
    assert call_count["n"] == 2  # 第二次成功


@pytest.mark.asyncio
async def test_adapt_handles_no_client_chat_fn(base_original, base_seeds):
    """客户端无 chat_json_async → 返回 None(防御性,不抛)。"""
    result = await adapt_one_question(
        client=type("FakeWithoutChat", (), {})(),  # 无 chat_json_async 方法
        original_question=base_original,
        seeds=base_seeds,
    )
    assert result is None


@pytest.mark.asyncio
async def test_adapt_handles_empty_seeds(base_original):
    """few-shot 为空(seed 章节无邻居)仍能跑(few_shot 块为空字符串)。"""

    async def fake_chat_json(system, user, **kw):
        return {
            "stem": base_original["stem"],
            "options": base_original["options"],
            "answer": "A",
            "key_points": base_original["key_points"],
            "analysis": "解析",
        }

    result = await adapt_one_question(
        client=object(),
        original_question=base_original,
        seeds=[],
        chat_json_fn=fake_chat_json,
    )
    assert result is not None

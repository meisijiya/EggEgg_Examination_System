"""Phase 1.5.6 多项改进单测:题型分布(A) + chapter 分布(B) + peer_review confidence 重校准(C)。

测试目标:
- TYPE_DISTRIBUTION_TARGET 常量 + _pick_preferred_type helper
- DOCX_FILE_TO_CHAPTER + _source_file_to_chapter_code helper
- AIQuestionOutput.chapter_code 持久化
- _gen_question_agent 接收 preferred_type + chapter_code 并注入 user prompt
- _peer_review_agent confidence 重校准:agree + delta==0 → 0.6 baseline; disagree 惩罚显式化
- multi_agent_q_gen batch-level type 追踪(lock protected)
- auto_approve_ai 默认 clean flag 逻辑

策略:用 mock LLM(记录调用 + 脚本化返回),完全隔离真 DeepSeek API。
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

PROJECT_TEST_ROOT = Path(__file__).resolve().parent
BACKEND_ROOT = PROJECT_TEST_ROOT.parent  # packages/backend
REPO_ROOT = BACKEND_ROOT.parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

SRC_PATH = BACKEND_ROOT / "app" / "services" / "corporate_strategy_q_gen.py"


# ---------------------------------------------------------------------------
# Module loading(沿用 test_corporate_strategy_q_gen.py 模式)
# ---------------------------------------------------------------------------


def _load_module():
    if "app.services.corporate_strategy_q_gen" in sys.modules:
        return sys.modules["app.services.corporate_strategy_q_gen"]
    import importlib
    backend_root = str(SRC_PATH.parents[2])
    if backend_root not in sys.path:
        sys.path.insert(0, backend_root)
    spec = importlib.util.spec_from_file_location(
        "app.services.corporate_strategy_q_gen", str(SRC_PATH)
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def cs():
    """加载 corporate_strategy_q_gen 模块。"""
    return _load_module()


# ---------------------------------------------------------------------------
# Test 1: TYPE_DISTRIBUTION_TARGET 常量 + 5 题型均匀
# ---------------------------------------------------------------------------


def test_type_distribution_target_constants(cs):
    """TYPE_DISTRIBUTION_TARGET 存在 + 5 题型 + 概率和 ~1.0。"""
    target = cs.TYPE_DISTRIBUTION_TARGET
    assert isinstance(target, dict)
    assert set(target.keys()) == {"single", "multi", "judge", "calc", "comprehensive"}
    total = sum(target.values())
    assert abs(total - 1.0) < 0.01, f"概率之和应为 1.0,got {total}"


# ---------------------------------------------------------------------------
# Test 2: _pick_preferred_type 选最欠缺题型
# ---------------------------------------------------------------------------


def test_pick_preferred_type_picks_deficit(cs):
    """当某 type 已经很多,优先选非该 type。"""
    pick = cs._pick_preferred_type

    # 全部为 0 → 任意;但 max 会选首次出现的(insertion-order)
    picked_0 = pick(
        {"single": 0, "multi": 0, "judge": 0, "calc": 0, "comprehensive": 0},
        n_total=20,
    )
    assert picked_0 in {"single", "multi", "judge", "calc", "comprehensive"}

    # single 已饱和 (4),其它都为 0 → 应选其它(不 single)
    picked_single_saturated = pick(
        {"single": 4, "multi": 0, "judge": 0, "calc": 0, "comprehensive": 0},
        n_total=20,
    )
    assert picked_single_saturated != "single", (
        f"single 已饱和(4≥20%*20=4),应选其它;got {picked_single_saturated}"
    )

    # 多个 type 都有 count,选 deficit 最大
    # n_total=10 → target=2 each;counts 已 = 各 2 → deficit = 0;or 偏置
    picked_balanced = pick(
        {"single": 5, "multi": 2, "judge": 2, "calc": 2, "comprehensive": 2},
        n_total=10,  # target=2 each
    )
    # single deficit = -3,其他 = 0;max 是 0(其他),ties 取 dict iter order 第一个
    assert picked_balanced != "single"


# ---------------------------------------------------------------------------
# Test 3: _source_file_to_chapter_code 派生
# ---------------------------------------------------------------------------


def test_source_file_to_chapter_code(cs):
    """DOCX source_file → chapter_code。"""
    fn = cs._source_file_to_chapter_code
    assert fn("PEST分析案例资料(1).docx") == "docx-pest"
    assert fn("企业战略(1).docx") == "docx-corp"
    assert fn("实证研究结构框架(1).docx") == "docx-empirical"
    assert fn("战略稳定性与文化适应性简答题(1).docx") == "docx-stab-adapt"
    assert fn("战略选择与实施案例资料(1).docx") == "docx-choice-impl"
    assert fn("探索战略创新的不同方面的主观题(1).docx") == "docx-innovation-subj"
    # 不认识的文件 → None
    assert fn("unknown.docx") is None
    assert fn("") is None
    # 兼容带路径前缀的 basename
    assert fn("/some/dir/企业战略(1).docx") == "docx-corp"
    assert fn("C:\\Users\\test\\企业战略(1).docx") == "docx-corp"


# ---------------------------------------------------------------------------
# Test 4: _gen_question_agent 接收 preferred_type + chapter_code + 持久化
# ---------------------------------------------------------------------------


class _SimpleMockLLM:
    """最小 mock LLM, 返回固定 JSON 或 抛出。"""

    configured = True

    def __init__(self, response: dict | None = None, raise_on_call: bool = False):
        self.response = response
        self.raise_on_call = raise_on_call
        self.call_count = 0
        self.last_user_prompt: str | None = None

    async def chat_json_async(self, system: str, user: str, *, timeout: int = 15):
        self.call_count += 1
        self.last_user_prompt = user
        if self.raise_on_call:
            raise RuntimeError("mock LLM down")
        return self.response or {
            "stem": "默认题: 关于战略选择的下列说法,正确的是?",
            "type": "single",
            "options": ["A.X", "B.Y", "C.Z", "D.W"],
            "answer": "B",
            "key_points": ["考点1", "考点2"],
            "analysis": "解析: X 不对因为 ...",
            "difficulty_hint": 2,
        }


@pytest.mark.asyncio
async def test_gen_question_passes_chapter_and_type_hint(cs):
    """_gen_question_agent 接收 preferred_type + chapter_code,user prompt 包含 hint。"""
    seg = cs.SegmentInput(
        id="q-pref-1",
        source_file="企业战略(1).docx",
        paragraph_index=5,
        raw_text="森旺股份是水果零售连锁企业...",
        needs_ai_answer=True,
    )
    mock = _SimpleMockLLM()
    result = await cs._gen_question_agent(
        mock,
        seg,
        preferred_type="comprehensive",
        chapter_code="docx-corp",
    )

    assert result is not None
    # 改进 B: chapter_code 持久化
    assert result.chapter_code == "docx-corp", (
        f"AIQuestionOutput.chapter_code 应为 docx-corp,got {result.chapter_code}"
    )
    # 改进 A: user prompt 含推荐题型 hint
    assert "推荐题型: comprehensive" in mock.last_user_prompt, (
        "user prompt 应包含 '推荐题型: comprehensive'"
    )
    # 改进 B: chapter context 注入 user prompt
    assert "docx-corp" in mock.last_user_prompt
    assert "企业战略案例" in mock.last_user_prompt


@pytest.mark.asyncio
async def test_gen_question_no_preferred_no_hint(cs):
    """不传 preferred_type → user prompt 不含 type hint(向后兼容)。"""
    seg = cs.SegmentInput(
        id="q-no-pref",
        source_file="x.docx",
        paragraph_index=0,
        raw_text="dummy content",
        needs_ai_answer=True,
    )
    mock = _SimpleMockLLM()
    result = await cs._gen_question_agent(mock, seg)
    assert result is not None
    # No chapter derived from unknown source_file
    assert result.chapter_code is None
    # No type hint
    assert "推荐题型:" not in mock.last_user_prompt


# ---------------------------------------------------------------------------
# Test 5: peer_review confidence 重校准(改进 C)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_peer_review_agree_zero_delta_confidence_baseline(cs):
    """agree + delta==0 → confidence max(conf, 0.6)(基线兜底,防诚实 0.0 永远不 approved)。"""
    q = cs.AIQuestionOutput(
        id="pr-agree-0",
        source_ref={"file": "x.docx", "paragraph_index": 0, "snippet": "..."},
        type="single",
        stem="题干",
        options=["A.x", "B.y"],
        answer="A",
        key_points=["x"],
        analysis="...",
        difficulty=2,
        confidence=0.0,  # 起始为 0
    )
    mock = _SimpleMockLLM(response={
        "agree": True,
        "confidence_delta": 0.0,  # delta = 0,关键测试点
        "disagreement_reasons": [],
        "key_points_gaps": [],
    })
    await cs._peer_review_agent(mock, q)
    # confidence 升到 ≥ 0.6
    assert q.confidence >= 0.6, (
        f"agree + delta==0 应 baseline 0.6,got {q.confidence}"
    )
    # needs_manual_review 不变
    assert q.needs_manual_review is False


@pytest.mark.asyncio
async def test_peer_review_disagree_pending(cs):
    """disagree → needs_manual_review=True + confidence 下降 → status=pending。"""
    q = cs.AIQuestionOutput(
        id="pr-disagree",
        source_ref={"file": "x.docx", "paragraph_index": 0, "snippet": "..."},
        type="single",
        stem="题干",
        options=["A.x", "B.y"],
        answer="A",
        key_points=["x"],
        analysis="...",
        difficulty=2,
        confidence=0.7,  # 即使起始 ≥ 0.6,disagree 也降
    )
    mock = _SimpleMockLLM(response={
        "agree": False,
        "confidence_delta": -0.5,
        "disagreement_reasons": ["答案与原资料矛盾"],
        "key_points_gaps": ["缺关键定义"],
    })
    await cs._peer_review_agent(mock, q)
    assert q.needs_manual_review is True
    # disagree + penalty → 0.7 - 0.5 = 0.2
    assert q.confidence == pytest.approx(0.2, abs=0.05)
    # needs_manual_review=True → 后续 status=pending (在 _process_one_segment)
    assert "peer_review_disagree" in q.review_reason
    assert "缺关键定义" in q.review_reason


# ---------------------------------------------------------------------------
# Test 6: multi_agent_q_gen batch-level type tracking + 平衡
# ---------------------------------------------------------------------------


class _RespectTypeHintMock:
    """Mock LLM that respects '推荐题型: X' in user prompt(模拟 well-instructed LLM)。

    Peer review call 也区分 system prompt 字符串(`审查员` 关键词)返回 agree + delta=0
    → 触发 Phase 1.5.6 Improvement C(agree + delta==0 → confidence baseline 0.6)。
    """

    configured = True

    def __init__(self):
        self.call_count = 0
        self.type_seen: list[str] = []

    async def chat_json_async(self, system: str, user: str, *, timeout: int = 15):
        self.call_count += 1

        # Peer review call(PEER_REVIEW_SYSTEM 含"审查员"关键词)
        if "审查员" in system:
            return {
                "agree": True,
                "confidence_delta": 0.0,  # 触发 baseline 提升
                "disagreement_reasons": [],
                "key_points_gaps": [],
            }

        # Gen call — 解析 user prompt 中的推荐题型
        preferred = "single"
        if "推荐题型: multi" in user:
            preferred = "multi"
        elif "推荐题型: judge" in user:
            preferred = "judge"
        elif "推荐题型: calc" in user:
            preferred = "calc"
        elif "推荐题型: comprehensive" in user:
            preferred = "comprehensive"
        self.type_seen.append(preferred)

        # 主观题 options=null
        options = ["A.X", "B.Y", "C.Z", "D.W"] if preferred in {"single", "multi", "judge"} else None
        return {
            "stem": f"题目: 推荐 {preferred} 型, 讨论战略选择。",
            "type": preferred,
            "options": options,
            "answer": "B",
            "key_points": [f"考点 {preferred}-1", f"考点 {preferred}-2"],
            "analysis": f"针对 {preferred} 的解析",
            "difficulty_hint": 2,
        }


@pytest.mark.asyncio
async def test_multi_agent_type_distribution_balanced(cs):
    """mock LLM respects type hint;10 segment pipeline 输出覆盖 5 题型。"""
    segments = [
        cs.SegmentInput(
            id=f"seg-{i}",
            source_file=[
                "PEST分析案例资料(1).docx",
                "企业战略(1).docx",
                "实证研究结构框架(1).docx",
                "战略稳定性与文化适应性简答题(1).docx",
                "战略选择与实施案例资料(1).docx",
                "探索战略创新的不同方面的主观题(1).docx",
            ][i % 6],
            paragraph_index=i,
            raw_text=f"段落 {i} 内容...",
            needs_ai_answer=True,
        )
        for i in range(10)
    ]
    mock = _RespectTypeHintMock()

    results = await cs.multi_agent_q_gen(
        segments,
        deepseek_client=mock,
        concurrency=4,
        do_web_search=False,
    )
    assert len(results) == 10
    # 验证 type 分布覆盖 ≥ 4 of 5 types
    type_dist = Counter(r.type for r in results)
    distinct = set(type_dist.keys())
    # 10 segments → 预期每 type 至少 1-3 个,覆盖 5 题型完全
    # (但 mock round-robin 不一定 100% 覆盖;断言至少 4 种)
    assert len(distinct) >= 4, (
        f"期望至少 4 种 type,got {len(distinct)}: {type_dist}"
    )

    # 验证 chapter_code 持久化(改进 B)
    for r in results:
        assert r.chapter_code is not None, (
            f"chapter_code 应派生自 source_file,got None for source={r.source_ref.get('file')}"
        )

    # 验证 confidence 重校准(agree + delta=0 → baseline 0.6,无 web search)
    for r in results:
        assert r.confidence >= 0.6, (
            f"confidence 应 ≥ 0.6(改进 C baseline),got {r.confidence} for id={r.id}"
        )


# ---------------------------------------------------------------------------
# Test 7: auto_approve_ai default logic (clean flag = approve)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def auto_approve_mod():
    """加载 auto_approve_ai 脚本(隔离 main 全局副作用)。"""
    spec = importlib.util.spec_from_file_location(
        "test_auto_approve_mod_v156",
        str(BACKEND_ROOT / "scripts" / "auto_approve_ai.py"),
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_auto_approve_clean_row_approved(auto_approve_mod):
    """Phase 1.5.6:confidence=0 + 无 review_reason → approved(clean flag 兜底)。"""
    rows = [
        {
            "id": "x1",
            "confidence": 0.0,           # 起始 0
            "needs_manual_review": False, # clean
            "review_reason": None,        # 无 reason
            "status": "pending",
        },
    ]
    out, counts = auto_approve_mod._process_rows(rows)
    assert counts["promoted_to_approved"] == 1
    promoted = [r for r in out if r["status"] == "approved"]
    assert len(promoted) == 1
    # approved 后 review_reason 清空 + needs_manual_review=False
    assert promoted[0]["review_reason"] is None
    assert promoted[0]["needs_manual_review"] is False


def test_auto_approve_dirty_row_pending(auto_approve_mod):
    """任何非 clean 字段(needs_review=True OR reason set)→ pending。"""
    # needs_manual_review=True → pending
    rows_1 = [
        {"id": "y1", "confidence": 0.99, "needs_manual_review": True,
         "review_reason": None, "status": "pending"},
    ]
    out, c = auto_approve_mod._process_rows(rows_1)
    assert c["promoted_to_approved"] == 0
    assert c["kept_pending_needs_review"] == 1

    # review_reason set → pending (Phase 1.5.6 even with high confidence)
    rows_2 = [
        {"id": "y2", "confidence": 0.99, "needs_manual_review": False,
         "review_reason": "peer_review_disagree", "status": "pending"},
    ]
    out, c = auto_approve_mod._process_rows(rows_2)
    assert c["promoted_to_approved"] == 0
    assert c["kept_pending_review_reason"] == 1


def test_auto_approve_strict_mode_threshold(auto_approve_mod):
    """CLI --confidence-threshold 启 strict mode:clean row 但 conf < threshold → pending。"""
    rows_template = [
        {"id": "z1", "confidence": 0.30, "needs_manual_review": False,
         "review_reason": None, "status": "pending"},
    ]

    # 默认 0.0 → approve(用 deep copy 防 in-place 污染)
    rows_default = [dict(r) for r in rows_template]
    _, c_default = auto_approve_mod._process_rows(rows_default, confidence_threshold=0.0)
    assert c_default["promoted_to_approved"] == 1

    # strict mode 0.6 → pending(独立 rows 实例,不被上轮污染)
    rows_strict = [dict(r) for r in rows_template]
    _, c_strict = auto_approve_mod._process_rows(rows_strict, confidence_threshold=0.6)
    assert c_strict["promoted_to_approved"] == 0
    assert c_strict["kept_pending_low_confidence"] == 1, (
        f"strict mode (threshold=0.6) 应 bucket 到 low_confidence,got {c_strict}"
    )

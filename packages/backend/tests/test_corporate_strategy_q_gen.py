"""corporate_strategy_q_gen.py 单测(multi-agent pipeline 各阶段)。

测试目标:
- _gen_question_agent:LLM 返回合规 JSON → 返回结构化 AIQuestionOutput
- _gen_question_agent:LLM 抛异常 → needs_manual_review + reason
- _peer_review_agent:disagree → confidence 下调 + needs_manual_review
- _peer_review_agent:agree → status 可能 approved(如果其它条件满足)
- _web_search:DuckDuckGo 真实调用(网络不可用时优雅降级)
- multi_agent_q_gen:concurrency + Semaphore 限流
- multi_agent_q_gen:无客户端 → 所有 pending(deepseek_unconfigured)
- read_input_jsonl + write_output_jsonl round-trip
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest

# 让 corporate_strategy_q_gen.py 不触发 backend 服务的 sqlalchemy 链式 import
SRC_PATH = "/home/ljh2923/opencode-project/EggEgg_Examination_System/packages/backend/app/services/corporate_strategy_q_gen.py"


def _load_module():
    """加载 corporate_strategy_q_gen 模块(用包内正常 import 链)。

    backend app/ 已在 sys.path(由 pytest.ini pythonpath = . 配置),
    corporate_strategy_q_gen 通过 `from app.services.deepseek_client import`
    仅在 _amain_async() 内部调用,模块级不直接 import — 因此 import 副作用
    与 deepseek_client 模块无关。
    """
    if "app.services.corporate_strategy_q_gen" in sys.modules:
        return sys.modules["app.services.corporate_strategy_q_gen"]
    import importlib

    backend_root = str(Path(SRC_PATH).parents[2])  # packages/backend
    if backend_root not in sys.path:
        sys.path.insert(0, backend_root)
    spec = importlib.util.spec_from_file_location(
        "app.services.corporate_strategy_q_gen", SRC_PATH
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["app.services.corporate_strategy_q_gen"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def cs():
    """加载 corporate_strategy_q_gen 模块(整 module cache)。"""
    return _load_module()


# ---------------------------------------------------------------------------
# Mock LLM Client
# ---------------------------------------------------------------------------


class _MockLLM:
    """Mock DeepSeekClient,记录 chat_json_async 调用 + 返回脚本化 JSON。"""

    configured = True  # 类属性 — 兼容 multi_agent_q_gen 的 `configured` 检查

    def __init__(self, responses: list[dict] | None = None, raise_on_call: bool = False):
        self.responses = responses or []
        self.call_count = 0
        self.raise_on_call = raise_on_call
        self.calls: list[tuple[str, str]] = []

    async def chat_json_async(self, system: str, user: str, *, timeout: int = 15):
        self.call_count += 1
        self.calls.append((system, user))
        if self.raise_on_call:
            raise RuntimeError("mock LLM down")
        if self.responses:
            return self.responses.pop(0)
        # 默认 fallback:返回一个合格但简陋的题目 JSON
        return {
            "stem": "公司战略选择主要包括哪几种基本类型?(提问型 stub)",
            "type": "single",
            "options": ["A.成长型", "B.稳定型", "C.收缩型", "D.以上都是"],
            "answer": "D",
            "key_points": ["战略选择类型", "公司战略"],
            "analysis": "公司战略选择主要包括成长型、稳定型、收缩型三种基本类型。",
            "difficulty_hint": 1,
        }


# ---------------------------------------------------------------------------
# _gen_question_agent 单测
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gen_question_success(cs):
    """LLM 返回合规 JSON → 输出结构化字段(stem/answer/key_points 全填)。"""
    seg = cs.SegmentInput(
        id="q1",
        source_file="战略选择.docx",
        paragraph_index=0,
        raw_text="公司战略选择主要包括成长型、稳定型、收缩型三种基本类型。",
        needs_ai_answer=True,
    )
    mock = _MockLLM(responses=[{
        "stem": "公司战略选择主要包括以下哪几种类型?",
        "type": "multi",
        "options": ["A.成长型", "B.稳定型", "C.收缩型", "D.组合型"],
        "answer": "ABC",
        "key_points": ["战略选择类型", "成长战略"],
        "analysis": "三种基本战略选择类型。",
        "difficulty_hint": 2,
    }])
    result = await cs._gen_question_agent(mock, seg)
    assert result is not None
    assert result.stem == "公司战略选择主要包括以下哪几种类型?"
    assert result.answer == "ABC"
    assert result.type == "multi"
    assert result.difficulty == 2
    assert result.key_points == ["战略选择类型", "成长战略"]
    assert result.needs_manual_review is False


@pytest.mark.asyncio
async def test_gen_question_missing_stem_triggers_review(cs):
    """LLM 返回 JSON 但 stem 空 → needs_manual_review=True。"""
    seg = cs.SegmentInput(
        id="q2",
        source_file="x.docx",
        paragraph_index=0,
        raw_text="dummy content",
        needs_ai_answer=True,
    )
    mock = _MockLLM(responses=[{
        "stem": "",
        "answer": "A",
        "key_points": ["x"],
        "type": "single",
        "analysis": "...",
        "difficulty_hint": 1,
    }])
    result = await cs._gen_question_agent(mock, seg)
    assert result is not None
    assert result.needs_manual_review is True
    assert "stem" in result.review_reason or "no_stem" in result.review_reason


@pytest.mark.asyncio
async def test_gen_question_llm_exception(cs):
    """LLM 抛异常 → needs_manual_review + question_gen_failure reason。"""
    seg = cs.SegmentInput(
        id="q3",
        source_file="x.docx",
        paragraph_index=0,
        raw_text="dummy content",
        needs_ai_answer=True,
    )
    mock = _MockLLM(raise_on_call=True)
    result = await cs._gen_question_agent(mock, seg)
    assert result is not None
    assert result.needs_manual_review is True
    assert "question_gen_failure" in result.review_reason


@pytest.mark.asyncio
async def test_gen_question_normalizes_invalid_type(cs):
    """LLM 返回 type=single-typo → 归一为 'calc'。"""
    seg = cs.SegmentInput(
        id="q4",
        source_file="x.docx",
        paragraph_index=0,
        raw_text="content",
        needs_ai_answer=True,
    )
    mock = _MockLLM(responses=[{
        "stem": "Test?",
        "type": "single-typo",
        "answer": "A",
        "key_points": ["x"],
        "analysis": "...",
        "difficulty_hint": 1,
    }])
    result = await cs._gen_question_agent(mock, seg)
    assert result.type == "calc"


# ---------------------------------------------------------------------------
# _peer_review_agent 单测
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_peer_review_disagree_triggers_review(cs):
    """peer_review 标 disagree → needs_manual_review=True + confidence 下调。"""
    seg = cs.SegmentInput(
        id="q5",
        source_file="x.docx",
        paragraph_index=0,
        raw_text="原资料片段",
        needs_ai_answer=True,
    )
    q = cs.AIQuestionOutput(
        id="q5",
        source_ref={"file": "x.docx", "paragraph_index": 0, "snippet": "..."},
        type="single",
        stem="题干",
        options=["A.x", "B.y"],
        answer="A",
        key_points=["考点"],
        analysis="...",
        difficulty=2,
        confidence=0.5,
    )
    mock = _MockLLM(responses=[{
        "agree": False,
        "confidence_delta": -0.3,
        "disagreement_reasons": ["key_points 不覆盖资料原文"],
        "key_points_gaps": ["缺战略类型定义"],
    }])
    await cs._peer_review_agent(mock, q)
    assert q.needs_manual_review is True
    assert q.confidence == 0.2  # 0.5 - 0.3
    assert "peer_review_disagree" in q.review_reason


@pytest.mark.asyncio
async def test_peer_review_agree_raises_confidence(cs):
    """peer_review 标 agree → confidence 可以超 0.6 → status=approved。"""
    seg = cs.SegmentInput(
        id="q6",
        source_file="x.docx",
        paragraph_index=0,
        raw_text="...",
        needs_ai_answer=True,
    )
    q = cs.AIQuestionOutput(
        id="q6",
        source_ref={"file": "x.docx", "paragraph_index": 0, "snippet": "..."},
        type="single",
        stem="题干",
        options=["A.x"],
        answer="A",
        key_points=["x"],
        analysis="...",
        difficulty=2,
        confidence=0.65,
    )
    mock = _MockLLM(responses=[{
        "agree": True,
        "confidence_delta": 0.2,
        "disagreement_reasons": [],
        "key_points_gaps": [],
    }])
    await cs._peer_review_agent(mock, q)
    # _process_one_segment 才会 determine status; 这里只测 confidence 上调
    assert q.confidence == pytest.approx(0.85, abs=0.05)
    # 这里我们没调 _process_one_segment,所以 status 保持 "pending"
    assert q.status == "pending"


# ---------------------------------------------------------------------------
# _web_search 集成测试(网络不可用时 graceful degrade)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_web_search_empty_query_returns_empty(cs):
    """空 query → 立即返回 []。"""
    import httpx
    async with httpx.AsyncClient():
        result = await cs._web_search("")
        assert result == []


@pytest.mark.asyncio
async def test_web_search_query_returns_list_or_empty(cs):
    """非空 query → 返回 list(0~3 项);网络不可时 = []。"""
    import httpx
    async with httpx.AsyncClient():
        result = await cs._web_search("公司战略 风险管理", max_results=2)
        # 网络可能不可用 — 验证返回类型合法,不强求 >0
        assert isinstance(result, list)
        assert len(result) <= 2
        for s in result:
            assert isinstance(s, str)
            assert len(s) <= 200


# ---------------------------------------------------------------------------
# multi_agent_q_gen 集成测试
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_agent_no_client_all_pending(cs):
    """deepseek_client=None → 所有结果 pending + deepseek_unconfigured reason。"""
    segments = [
        cs.SegmentInput(id=f"q{i}", source_file="x.docx", paragraph_index=i, raw_text=f"text-{i}", needs_ai_answer=True)
        for i in range(3)
    ]
    results = await cs.multi_agent_q_gen(
        segments,
        deepseek_client=None,
        concurrency=2,
        do_web_search=False,
    )
    assert len(results) == 3
    for r in results:
        assert r.status == "pending"
        assert r.needs_manual_review is True
        assert r.review_reason == "deepseek_unconfigured"


@pytest.mark.asyncio
async def test_multi_agent_with_mock_full_pipeline(cs):
    """mock LLM Gen + Peer 全 success path → confidence = 0.2(默认 + peer +0.2)。

    注:不使用 do_web_search=True — 测试 sandbox 网络可能阻塞 DuckDuckGo,
    _web_grounding_agent 早退不影响 confidence 调整路径正确性(单独测 Gen+Web+Peer)。
    """
    segments = [
        cs.SegmentInput(
            id="qq1",
            source_file="x.docx",
            paragraph_index=0,
            raw_text="公司战略选择主要包括成长型、稳定型、收缩型三种基本类型。",
            needs_ai_answer=True,
        ),
    ]
    mock = _MockLLM(responses=[
        # Gen
        {
            "stem": "公司战略选择包括哪些类型?",
            "type": "multi",
            "options": ["A.成长型", "B.稳定型", "C.收缩型", "D.以上都是"],
            "answer": "ABCD",
            "key_points": ["战略选择类型", "公司战略"],
            "analysis": "战略选择包括成长、稳定、收缩。",
            "difficulty_hint": 1,
        },
        # Peer review (agree + delta +0.2)
        {
            "agree": True,
            "confidence_delta": 0.2,
            "disagreement_reasons": [],
            "key_points_gaps": [],
        },
    ])

    results = await cs.multi_agent_q_gen(
        segments,
        deepseek_client=mock,
        concurrency=2,
        do_web_search=False,  # 网络独立性;Web grounding 测单独覆盖
    )

    assert len(results) == 1
    r = results[0]
    # Gen: confidence 0.0(默认)
    # Peer delta +0.2 → 0.2
    assert r.confidence == pytest.approx(0.2, abs=0.05)
    # 0.2 < 0.6 → status=pending
    assert r.status == "pending"
    assert r.needs_manual_review is False  # 没 disagreement
    assert mock.call_count == 2  # Gen + Peer 两次调用


@pytest.mark.asyncio
async def test_multi_agent_concurrency_limit(cs):
    """concurrency=2 时多 segment 串行(模拟;无法严格测并发数但验证 Semaphore 不出错)。"""
    mock = _MockLLM(raise_on_call=True)  # Gen 阶段全 fail
    segments = [
        cs.SegmentInput(
            id=f"seg{i}",
            source_file="x.docx",
            paragraph_index=i,
            raw_text=f"content {i}",
            needs_ai_answer=True,
        )
        for i in range(5)
    ]
    results = await cs.multi_agent_q_gen(
        segments,
        deepseek_client=mock,
        concurrency=2,
        do_web_search=False,
    )
    assert len(results) == 5
    for r in results:
        # Gen 失败 → needs_manual_review
        assert "question_gen_failure" in r.review_reason or "no_stem" in r.review_reason


# ---------------------------------------------------------------------------
# JSONL round-trip
# ---------------------------------------------------------------------------


def test_jsonl_roundtrip(cs, tmp_path):
    """read_input_jsonl + write_output_jsonl 往返保真。"""
    seg_path = tmp_path / "segs.jsonl"
    out_path = tmp_path / "out.jsonl"
    seg_path.write_text(
        json.dumps(
            {
                "id": "abc",
                "source_file": "test.docx",
                "paragraph_index": 3,
                "raw_text": "段落内容",
                "section_path": "战略选择",
                "needs_ai_answer": True,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    segments = cs.read_input_jsonl(seg_path)
    assert len(segments) == 1
    seg = segments[0]
    assert seg.id == "abc"
    assert seg.source_file == "test.docx"
    assert seg.paragraph_index == 3
    assert seg.raw_text == "段落内容"

    q_out = cs.AIQuestionOutput(
        id="abc",
        source_ref={"file": "test.docx", "paragraph_index": 3, "snippet": "..."},
        type="calc",
        stem="test stem",
        options=None,
        answer="x",
        key_points=["y"],
        analysis="z",
        confidence=0.5,
        needs_manual_review=True,
        status="pending",
        review_reason="test",
    )
    n = cs.write_output_jsonl([q_out], out_path)
    assert n == 1
    lines = out_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["id"] == "abc"
    assert row["type"] == "calc"
    assert row["needs_manual_review"] is True
    assert row["status"] == "pending"


def test_jsonl_input_empty_file(cs, tmp_path):
    """空 JSONL → read returns []。"""
    seg_path = tmp_path / "empty.jsonl"
    seg_path.write_text("", encoding="utf-8")
    assert cs.read_input_jsonl(seg_path) == []


def test_jsonl_input_missing_file(cs, tmp_path):
    """不存在的 JSONL → read returns [](graceful)。"""
    assert cs.read_input_jsonl(tmp_path / "nope.jsonl") == []

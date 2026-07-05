"""判分器测试 — 客观题矩阵 + 主观题阈值边界 + sub_answer 拆解（fix-17）。

fix-30 P0 扩展:
- _grade_short_answer / _grade_case_analysis(独立函数)
- grade_answer dispatcher 7 题型路由
- 回归保护:adapted payload 闭环 / mixed mode 性能 / 5 题型 routing 参数化

Phase 2-Lane-B 改造:
- comment 不再断言具体文本("回答正确"/"正确答案:X"/"完整覆盖"/"过短"等)
- 改为:comment ∈ PraiseService.pool[scenario](验证评语从池中选取)
- 行为断言(awarded_score / is_correct / sub_answer_count / missed_points)保留
"""
from __future__ import annotations

import time

import pytest

from app.services.grader import (
    _grade_case_analysis,
    _grade_short_answer,
    _grade_subjective,
    grade_answer,
    parse_key_points,
    parse_sub_answers,
    parse_sub_answers_to_map,
)
from app.services.praise_service import get_praise_service


def _assert_praise_comment(result, scenario: str) -> None:
    """Phase 2-Lane-B:验证 comment 是 praise pool 中对应场景的字符串。

    参数:
        result: GradedAnswer 对象
        scenario: 'unanswered' / 'correct' / 'wrong'(决定查哪个 pool)
    """
    pool = get_praise_service().pool[scenario]
    assert result.comment in pool, (
        f"comment={result.comment!r} 不在 {scenario} pool={pool}"
    )


def _reset_praise_history() -> None:
    """清空 praise LRU(避免 test 间 pick 顺序干扰断言)。"""
    get_praise_service().reset()


# ---------- 客观题矩阵 ----------


class TestSingleChoice:
    """单选题边界。"""

    def test_correct(self):
        _reset_praise_history()
        r = grade_answer("single", "A", "A", 2.0)
        assert r.awarded_score == 2.0
        assert r.is_correct is True
        _assert_praise_comment(r, "correct")

    def test_wrong(self):
        _reset_praise_history()
        r = grade_answer("single", "A", "B", 2.0)
        assert r.awarded_score == 0.0
        assert r.is_correct is False
        _assert_praise_comment(r, "wrong")

    def test_empty(self):
        _reset_praise_history()
        r = grade_answer("single", "A", "", 2.0)
        assert r.awarded_score == 0.0
        _assert_praise_comment(r, "unanswered")

    def test_none(self):
        _reset_praise_history()
        r = grade_answer("single", "A", None, 2.0)
        assert r.awarded_score == 0.0
        _assert_praise_comment(r, "unanswered")


class TestMultiChoice:
    """多选题 — 漏选/多选/错选 都应判错。"""

    def test_full_correct(self):
        _reset_praise_history()
        r = grade_answer("multi", "ABD", "ABD", 3.0)
        assert r.awarded_score == 3.0
        assert r.is_correct is True
        _assert_praise_comment(r, "correct")

    def test_correct_with_separator(self):
        # 学员用逗号分隔
        _reset_praise_history()
        r = grade_answer("multi", "ABD", "A,B,D", 3.0)
        assert r.awarded_score == 3.0
        assert r.is_correct is True

    def test_missing_one(self):
        # 漏选 B → 错
        _reset_praise_history()
        r = grade_answer("multi", "ABD", "AD", 3.0)
        assert r.awarded_score == 0.0
        assert r.is_correct is False
        _assert_praise_comment(r, "wrong")

    def test_extra_one(self):
        # 多选 C → 错
        _reset_praise_history()
        r = grade_answer("multi", "ABD", "ABCD", 3.0)
        assert r.awarded_score == 0.0
        assert r.is_correct is False

    def test_empty(self):
        _reset_praise_history()
        r = grade_answer("multi", "ABD", "", 3.0)
        assert r.awarded_score == 0.0
        _assert_praise_comment(r, "unanswered")


class TestJudge:
    """判断题 — 文本答案比对（对/错）。"""

    def test_correct(self):
        _reset_praise_history()
        r = grade_answer("judge", "对", "对", 1.0)
        assert r.awarded_score == 1.0
        assert r.is_correct is True
        _assert_praise_comment(r, "correct")

    def test_wrong(self):
        _reset_praise_history()
        r = grade_answer("judge", "对", "错", 1.0)
        assert r.awarded_score == 0.0
        assert r.is_correct is False
        _assert_praise_comment(r, "wrong")


# ---------- 主观题阈值边界 ----------


class TestSubjectiveThresholds:
    """主观题关键词覆盖率阈值边界。"""

    KEY_POINTS = ["债券价值", "面值", "市场利率", "票面利率", "现值系数"]

    def test_empty_answer(self):
        _reset_praise_history()
        r = grade_answer("calc", "参考答案", "", 5.0, key_points=self.KEY_POINTS)
        assert r.awarded_score == 0.0
        _assert_praise_comment(r, "unanswered")

    def test_short_answer_below_threshold(self):
        # < 5 字 → 0 分(wrong 场景)
        _reset_praise_history()
        r = grade_answer("calc", "参考答案", "对", 5.0, key_points=self.KEY_POINTS)
        assert r.awarded_score == 0.0
        _assert_praise_comment(r, "wrong")

    def test_full_coverage(self):
        # 全部关键点都覆盖 → 满分
        _reset_praise_history()
        ans = "债券价值=面值×票面利率×现值系数，市场利率影响折现率"
        r = grade_answer("calc", "参考答案", ans, 5.0, key_points=self.KEY_POINTS)
        assert r.awarded_score == 5.0
        assert r.is_correct is True
        _assert_praise_comment(r, "correct")
        assert r.missed_points is None  # 满分无未覆盖

    def test_just_below_threshold(self):
        # 4/5 = 80% — 介于 0.6 和 1.0 之间
        _reset_praise_history()
        ans = "债券价值=面值×票面利率×现值系数"  # 缺"市场利率"
        r = grade_answer("calc", "参考答案", ans, 5.0, key_points=self.KEY_POINTS)
        assert r.awarded_score == 4.0  # 80% × 5
        assert r.is_correct is True
        _assert_praise_comment(r, "wrong")  # coverage < 1.0
        assert r.missed_points is not None
        assert "市场利率" in r.missed_points

    def test_exact_threshold(self):
        # 3/5 = 60% — 刚好达门槛
        _reset_praise_history()
        ans = "债券价值公式中含面值与现值系数两个关键变量"  # 含 债券价值, 面值, 现值系数
        r = grade_answer("calc", "参考答案", ans, 5.0, key_points=self.KEY_POINTS)
        assert r.awarded_score == 3.0  # 60% × 5
        assert r.is_correct is True

    def test_below_threshold(self):
        # 2/5 = 40% < 60% → 0 分
        _reset_praise_history()
        ans = "债券价值通过现值系数计算"
        r = grade_answer("calc", "参考答案", ans, 5.0, key_points=self.KEY_POINTS)
        assert r.awarded_score == 0.0
        _assert_praise_comment(r, "wrong")

    def test_zero_coverage(self):
        _reset_praise_history()
        ans = "这个答案与题目完全没有关系，纯粹瞎编一些内容凑字数"
        r = grade_answer("calc", "参考答案", ans, 5.0, key_points=self.KEY_POINTS)
        assert r.awarded_score == 0.0
        _assert_praise_comment(r, "wrong")

    def test_empty_key_points_fallback(self):
        # key_points 缺失 + 完全匹配参考答案 → 满分(correct 场景)
        _reset_praise_history()
        ans = "参考答案文本"
        r = grade_answer("calc", "参考答案文本", ans, 5.0, key_points=None)
        assert r.awarded_score == 5.0
        assert r.is_correct is True
        _assert_praise_comment(r, "correct")

    def test_empty_key_points_mismatch(self):
        # ≥ 5 字才进入 key_points 退化分支 → wrong
        _reset_praise_history()
        r = grade_answer("calc", "参考答案", "学员给的答案文本", 5.0, key_points=None)
        assert r.awarded_score == 0.0
        _assert_praise_comment(r, "wrong")


class TestCustomCoverageThreshold:
    """MIN_COVERAGE 自定义阈值。"""

    KEY_POINTS = ["A", "B", "C", "D", "E"]

    def test_50pct_with_threshold_0_5(self):
        # 3/5 = 60% ≥ 0.5 → 给分
        ans = "包含 A B C 这三个关键词的内容"
        r = grade_answer("calc", "x", ans, 5.0, key_points=self.KEY_POINTS, min_coverage=0.5)
        # 含 A, B, C = 3/5 = 60%
        assert r.awarded_score == 3.0
        assert r.is_correct is True

    def test_50pct_with_threshold_0_7(self):
        # 3/5 = 60% < 0.7 → 不给分
        ans = "包含 A B C 这三个关键词的内容"
        r = grade_answer("calc", "x", ans, 5.0, key_points=self.KEY_POINTS, min_coverage=0.7)
        assert r.awarded_score == 0.0


# ---------- parse_key_points 工具 ----------


class TestParseKeyPoints:
    """DB JSON 解析工具。"""

    def test_valid_json(self):
        assert parse_key_points('["a", "b", "c"]') == ["a", "b", "c"]

    def test_none(self):
        assert parse_key_points(None) is None

    def test_empty_string(self):
        assert parse_key_points("") is None

    def test_invalid_json(self):
        assert parse_key_points("not json") is None


# ---------- fix-17: sub_answer 拆解 ----------


class TestSubAnswerParsing:
    """parse_sub_answers — 三种格式自动识别。"""

    def test_numbered_format(self):
        # "1.年金现值=1000；2.终值=1000" → 2 sub
        subs = parse_sub_answers("1.年金现值=1000；2.终值=1000")
        assert len(subs) == 2
        assert subs[0] == "年金现值=1000"
        assert subs[1] == "终值=1000"

    def test_numbered_format_with_decimals(self):
        # 包含 0.95 / 1.05 这种内嵌数字不应被误识别为编号
        subs = parse_sub_answers(
            "1.年金现值系数0.95；2.终值系数1.05；3.折现率5%"
        )
        assert len(subs) == 3
        assert "年金现值系数" in subs[0]
        assert "终值系数" in subs[1]
        assert "折现率" in subs[2]

    def test_numbered_with_chinese_separator(self):
        # 兼容 "1、" 中文顿号
        subs = parse_sub_answers(
            "1、第一问年金现值；2、第二问终值；3、第三问折现率"
        )
        assert len(subs) == 3

    def test_numbered_with_paren(self):
        # 兼容 "1)" 括号
        subs = parse_sub_answers("1) 现值1000；2) 终值1050")
        assert len(subs) == 2

    def test_semicolon_only(self):
        # "现值1000；终值1050" → 2 sub（无编号）
        subs = parse_sub_answers("现值1000；终值1050")
        assert len(subs) == 2
        assert subs[0] == "现值1000"
        assert subs[1] == "终值1050"

    def test_semicolon_english(self):
        # 英文分号 ; 也识别
        subs = parse_sub_answers("pv=1000; fv=1050")
        assert len(subs) == 2

    def test_single_paragraph(self):
        # "年金现值1000元，终值1050元" → 1 sub（中文逗号不是分号）
        subs = parse_sub_answers("年金现值1000元，终值1050元")
        assert len(subs) == 1
        assert subs[0] == "年金现值1000元，终值1050元"

    def test_too_short(self):
        # 空 / 过短 → 空列表
        assert parse_sub_answers("") == []
        assert parse_sub_answers("abc") == []  # < 5 chars
        assert parse_sub_answers(None) == []  # type: ignore[arg-type]

    def test_mixed_numbered_and_unnumbered(self):
        # 1.x；y；2.z → 至少 2 段有编号，按编号格式拆（剥离编号前缀）
        subs = parse_sub_answers("1.第一问；中间补充；2.第三问")
        assert len(subs) == 3
        assert "第一问" in subs[0]
        assert "第三问" in subs[-1]

    def test_only_one_numbered_part(self):
        # 只有 1 段编号 → 走"按分号切"分支，保留编号前缀
        subs = parse_sub_answers("1.只有一段；后面无编号")
        # numbered_count=1 < 2, 走 raw_parts 分支
        assert len(subs) == 2

    def test_decimal_numbers_not_stripped(self):
        """regression: '4.5%' / '1.5元' 这类小数不应被识别为编号前缀。"""
        subs = parse_sub_answers("4.5%；5.25%；8% 三个关键利率")
        # 不应被剥离为 "5%" / "25%"
        assert subs == ["4.5%", "5.25%", "8% 三个关键利率"]

        subs = parse_sub_answers("1.5元；2.6元")
        assert subs == ["1.5元", "2.6元"]

    def test_numbered_with_decimal_content(self):
        """regression: '1.x0.95' 内嵌小数但整体是编号格式 → 正确剥离前缀。"""
        subs = parse_sub_answers("1.年金现值系数0.95；2.终值系数1.05；3.折现率5%")
        assert len(subs) == 3
        # 编号前缀应被剥离，但内容里的 0.95 / 1.05 保留
        assert subs[0] == "年金现值系数0.95"
        assert subs[1] == "终值系数1.05"
        assert subs[2] == "折现率5%"


class TestGradeSubjectiveWithSubAnswers:
    """主观题结合 sub_answers 拆解判分（fix-17）+ Phase 2-Lane-B praise 评语。"""

    def test_numbered_full_coverage(self):
        """编号格式 + 完整覆盖 key_points → 满分 + correct 评语池。"""
        kps = ["年金现值系数", "终值系数", "折现率"]
        user_answer = "1.年金现值系数0.95；2.终值系数1.05；3.折现率5%"
        r = grade_answer("calc", "参考答案", user_answer, 5.0, key_points=kps)
        assert r.awarded_score == 5.0
        assert r.is_correct is True
        _assert_praise_comment(r, "correct")
        assert r.sub_answer_count == 3
        assert r.missed_points is None  # 满分无未覆盖

    def test_numbered_partial_coverage(self):
        """编号格式 + 覆盖 2/3 → 部分给分 + missed_points 列出未覆盖 + wrong 评语。"""
        kps = ["年金现值系数", "终值系数", "折现率"]
        user_answer = "1.年金现值系数0.95；2.终值系数1.05"  # 缺"折现率"
        r = grade_answer("calc", "参考答案", user_answer, 5.0, key_points=kps)
        # coverage = 2/3 ≈ 0.67 ≥ 0.6 → 部分给分
        assert 0 < r.awarded_score < 5.0
        assert r.is_correct is True
        _assert_praise_comment(r, "wrong")
        assert r.sub_answer_count == 2
        assert r.missed_points is not None
        assert "折现率" in r.missed_points

    def test_no_numbering_still_works(self):
        """整段包含所有 key_points（向后兼容，无编号也满分）。"""
        kps = ["债券价值", "面值", "现值系数"]
        user_answer = "债券价值=面值×现值系数，结果是债券现值。"
        r = grade_answer("calc", "参考答案", user_answer, 5.0, key_points=kps)
        assert r.awarded_score == 5.0
        assert r.is_correct is True
        _assert_praise_comment(r, "correct")
        assert r.sub_answer_count is None  # 无编号 → 不显示分小问数

    def test_semicolon_only_full_coverage(self):
        """分号分隔 + 完整覆盖 → 满分(correct 评语)。"""
        kps = ["现值", "终值"]
        user_answer = "现值1000；终值1050"
        r = grade_answer("calc", "参考答案", user_answer, 5.0, key_points=kps)
        assert r.awarded_score == 5.0
        _assert_praise_comment(r, "correct")

    def test_missed_points_truncation(self):
        """未覆盖要点超过 80 字 → missed_points 截断(comment 不再含此文本)。

        Phase 2-Lane-B:
        - missed_points 字段保留,限制 ≤ 3 条
        - 80 字截断不再应用(comment 已不含此信息)
        - comment 评语走 wrong 池
        """
        kps = [
            "超长关键要点A" * 5,
            "超长关键要点B" * 5,
            "超长关键要点C" * 5,
            "短的D",
        ]
        user_answer = "1.短的D内容"  # 只覆盖 D
        r = grade_answer("calc", "参考答案", user_answer, 5.0, key_points=kps)
        # coverage = 1/4 = 0.25 < 0.6 → score=0
        assert r.awarded_score == 0.0
        _assert_praise_comment(r, "wrong")
        # missed_points 字段 ≤ 3 条
        if r.missed_points is not None:
            assert len(r.missed_points) <= 3

    def test_short_answer_returns_zero(self):
        """< 5 字 → 0 分(wrong 评语)。"""
        kps = ["A", "B", "C"]
        r = grade_answer("calc", "x", "短", 5.0, key_points=kps)
        assert r.awarded_score == 0.0
        _assert_praise_comment(r, "wrong")
        assert r.sub_answer_count is None
        assert r.missed_points is None


# ============================================================================
# fix-30 P0:_grade_short_answer 独立函数 + dispatcher 路由
# ============================================================================


class TestShortAnswerGrader:
    """_grade_short_answer 直接单测(不通过 dispatcher)+ Phase 2-Lane-B praise。

    关键不变性:
    - 完全复用 _compute_coverage + _build_comment_by_coverage(零重复实现)
    - 不拆 sub_answers(与 _grade_subjective 的核心差异)
    - < 5 字 → 0 分(wrong 评语)
    - > 200 字 → 软警告(Phase 2-Lane-B 后警告不再注入 comment)
    - key_points 缺失 → 退化参考答案完全匹配(评语 correct/wrong)
    """

    KEY_POINTS = ["SWOT分析", "战略选择", "竞争优势"]

    def _patch_stripped(self, user_answer):
        """共用 wrapper:_grade_short_answer 不返回 sub_answer_count。"""
        return _grade_short_answer(
            correct_answer="参考答案",
            user_answer=user_answer,
            full_score=5.0,
            key_points=self.KEY_POINTS,
            min_coverage=0.6,
        )

    def test_short_answer_full_score(self):
        """全关键点覆盖 → 满分(correct 评语)。"""
        _reset_praise_history()
        ans = "本案例通过SWOT分析得出可采用差异化战略选择形成竞争优势"
        r = self._patch_stripped(ans)
        assert r.awarded_score == 5.0
        assert r.is_correct is True
        _assert_praise_comment(r, "correct")
        # 关键:not _grade_subjective 的"识别到 N 个分小问作答"
        assert r.sub_answer_count is None  # short_answer 不拆 sub

    def test_short_answer_proportional(self):
        """部分覆盖 → 按比例给分(≥60% 门槛,wrong 评语)。"""
        _reset_praise_history()
        ans = "本案例应通过SWOT分析得出选择"  # 缺"战略选择"和"竞争优势"
        r = self._patch_stripped(ans)
        # 1/3 = 33% < 60% → 0
        assert r.awarded_score == 0.0
        assert r.is_correct is False
        _assert_praise_comment(r, "wrong")

    def test_short_answer_just_below_threshold(self):
        """2/3 ≈ 67% ≥ 60% → 按比例(wrong 评语,coverage < 1.0)。"""
        _reset_praise_history()
        ans = "本案例通过SWOT分析得出竞争优势"  # 覆盖 SWOT分析, 竞争优势
        r = self._patch_stripped(ans)
        # 2/3 ≈ 66.7% ≥ 60%
        assert r.awarded_score == pytest.approx(round(5.0 * 2 / 3, 1), abs=0.05)
        assert r.is_correct is True
        _assert_praise_comment(r, "wrong")

    def test_short_answer_too_short_returns_zero(self):
        """< 5 字(包含空白处理) → 0 分(wrong 评语)。"""
        _reset_praise_history()
        for short_input in ["a", "ab", "abcd", "  ", " 对 ", "短答"]:
            r = _grade_short_answer(
                correct_answer="x", user_answer=short_input,
                full_score=5.0, key_points=self.KEY_POINTS, min_coverage=0.6,
            )
            # 注意 " 对 "(空白 + 单字)经 strip 后 1 字 < 5
            if len(short_input.strip()) < 5:
                assert r.awarded_score == 0.0
                _assert_praise_comment(r, "wrong")
                _reset_praise_history()  # 每次重置防止 LRU 干扰

    def test_short_answer_long_answer_soft_warning(self):
        """> 200 字 → 全覆盖仍满分(correct 评语,长度警告不再注入 comment)。

        Phase 2-Lane-B:长度警告文本不再追加到 comment(由前端基于 user_answer
        长度自行判定或后续 polish 单独处理)。
        """
        _reset_praise_history()
        long_ans = "SWOT分析战略选择竞争优势" * 30  # ~12 chars × 30 = 360+
        assert len(long_ans) > 200
        r = self._patch_stripped(long_ans)
        # 全覆盖 → 满分
        assert r.awarded_score == 5.0
        _assert_praise_comment(r, "correct")

    def test_short_answer_no_key_points_fallback(self):
        """key_points 为空 → 退化参考答案完全匹配(correct 评语)。"""
        _reset_praise_history()
        ans = "标准答案原话此处完整"
        r = _grade_short_answer(
            correct_answer=ans, user_answer=ans,
            full_score=5.0, key_points=None, min_coverage=0.6,
        )
        assert r.awarded_score == 5.0
        _assert_praise_comment(r, "correct")

        # 不匹配 + 长度 ≥5 → 退化路径命中 wrong
        _reset_praise_history()
        r2 = _grade_short_answer(
            correct_answer=ans, user_answer="完全不相同的回答内容",
            full_score=5.0, key_points=None, min_coverage=0.6,
        )
        assert r2.awarded_score == 0.0
        _assert_praise_comment(r2, "wrong")


# ============================================================================
# fix-30 P0:_grade_case_analysis 独立函数 + parse_sub_answers_to_map
# ============================================================================


class TestCaseAnalysisGrader:
    """_grade_case_analysis 单测。

    rubric schema 兼容 Pydantic CaseRubric(CaseSubQuestion / CaseConclusion):
      {
        "sub_questions": [{"id": "1", "points": 3, "key_points": [...]}, ...],
        "conclusion": {"points": 3, "criteria": [...]}
      }
    """

    FULL_RUBRIC = {
        "sub_questions": [
            {"id": "1", "points": 3, "key_points": ["PEST分析", "宏观环境"]},
            {"id": "2", "points": 4, "key_points": ["竞争格局", "市场份额"]},
            {"id": "3", "points": 3, "key_points": ["战略选择"]},
        ],
        "conclusion": {"points": 3, "criteria": ["可执行建议", "风险提示"]},
    }

    def test_case_analysis_no_rubric_returns_zero(self):
        """rubric=None → 0 分 + wrong 评语(Phase 2-Lane-B 池化)。"""
        _reset_praise_history()
        r = _grade_case_analysis(
            user_answer="任何长答案都无所谓",
            rubric=None, full_score=13.0,
        )
        assert r.awarded_score == 0.0
        _assert_praise_comment(r, "wrong")
        assert r.per_sub_question_scores == []

    def test_case_analysis_empty_rubric_returns_zero(self):
        """rubric 既无 sub_questions 也无 conclusion → 0 分(wrong 评语)。"""
        _reset_praise_history()
        r = _grade_case_analysis(
            user_answer="内容不重要",
            rubric={"sub_questions": [], "conclusion": None},
            full_score=10.0,
        )
        assert r.awarded_score == 0.0
        _assert_praise_comment(r, "wrong")

    def test_case_analysis_full_coverage_sub_split(self):
        """完整 sub-by-sub 覆盖 → 满分(13/13 = 3+4+3+3)+ correct 评语池。

        设计:user 答案按 sub 分段,conclusion 通过整段(criteria)
        也命中,确保 sub 评分 + conclusion 评分都满分。
        """
        _reset_praise_history()
        # 用 ';' 分号切 4 段:sub1; sub2; sub3; conclusion
        # 注:rubric.sub[3] key_points=["战略选择"](连续词组),
        # user 必须含**连续**"战略选择"
        user = (
            "1.本案例通过PEST分析得出宏观环境的机遇与威胁;"
            "2.运用波特五力分析竞争格局判断市场份额;"
            "3.经过多种路径对比后选择战略选择,最终选择差异化战略构建品牌优势;"
            "结论:可执行建议聚焦细分市场;风险提示关注政策合规"
        )
        r = _grade_case_analysis(
            user_answer=user, rubric=self.FULL_RUBRIC, full_score=13.0,
        )
        # parse_sub_answers 切 4 段:['1.X','2.Y','3.Z','结论:...;...']
        # rubric.sub_questions = 3 → 前 3 个 sub 拿专属段;第 4 段忽略
        # conclusion 始终用整段(因不在 sub_questions 列表中)
        # 整段含 '可执行建议' 和 '风险提示' → 2/2 → 满分 3 分
        assert r.awarded_score == pytest.approx(13.0, abs=0.1), r.comment
        assert r.is_correct is True
        _assert_praise_comment(r, "correct")
        # per_sub_question_scores 校验
        assert r.per_sub_question_scores is not None
        sub_ids = [s["id"] for s in r.per_sub_question_scores]
        assert "1" in sub_ids and "2" in sub_ids and "3" in sub_ids and "conclusion" in sub_ids
        # 每项 sub 应该有 matched == total(全命中)
        for s in r.per_sub_question_scores:
            if s["id"] != "conclusion":
                assert s["matched"] == s["total"], (
                    f"sub_id={s['id']} 未全命中: matched={s['matched']} total={s['total']}"
                )
        # 总结论项格式
        conclusion_row = next(s for s in r.per_sub_question_scores if s["id"] == "conclusion")
        assert conclusion_row["matched"] == conclusion_row["total"]


    def test_case_analysis_per_sub_partial(self):
        """部分 sub 答对、部分不对 → 按 sub 各自计算 + 总分 cap。"""
        _reset_praise_history()
        user = (
            "1.本案例通过PEST分析得出宏观环境的机遇与威胁;"
            "2.竞争格局方面市场份额较高;"
            "3.战略选择问题无明确信息"  # sub 3 显式答但不含"战略选择"关键词
        )
        r = _grade_case_analysis(
            user_answer=user, rubric=self.FULL_RUBRIC, full_score=13.0,
        )
        assert r.per_sub_question_scores is not None
        # sub 1: ['PEST分析', '宏观环境'] → 命中 2/2 → 3 pts(满分)
        sub_1 = next(s for s in r.per_sub_question_scores if s["id"] == "1")
        assert sub_1["awarded"] == 3.0
        assert sub_1["matched"] == 2
        # sub 2: ['竞争格局', '市场份额'] → sub text "竞争格局方面市场份额较高"
        # 去停用词后命中"竞争格局"和"市场份额" → 4 pts(满分)
        sub_2 = next(s for s in r.per_sub_question_scores if s["id"] == "2")
        assert sub_2["awarded"] == 4.0
        # sub 3: ['战略选择'] → sub text "战略选择问题无明确信息"
        # 含 "战略选择" → 3 pts(满分)
        sub_3 = next(s for s in r.per_sub_question_scores if s["id"] == "3")
        assert sub_3["matched"] == 1
        # conclusion: criteria=['可执行建议','风险提示']
        # 整段 = "1.X;2.Y;3.Z",去停用词 → "1.X 2.Y 3.Z" + "战略选择问题无明确信息"
        # 不含'可执行建议'与'风险提示' → 0 分
        conclusion_row = next(s for s in r.per_sub_question_scores if s["id"] == "conclusion")
        assert conclusion_row["awarded"] == 0.0

    def test_case_analysis_misaligned_sub_id_position(self):
        """用户答案切分段数 ≠ rubric sub 数 → 多余 ignore,缺失 fallback。"""
        short_rubric = {
            "sub_questions": [
                {"id": "1", "points": 5, "key_points": ["A"]},
                {"id": "2", "points": 5, "key_points": ["B"]},
                {"id": "3", "points": 5, "key_points": ["C"]},
            ]
        }
        # 切 2 段(漏答 sub 3)
        user = "1.A内容;2.B内容"
        r = _grade_case_analysis(user_answer=user, rubric=short_rubric, full_score=15.0)
        assert r.per_sub_question_scores is not None
        sub_1 = next(s for s in r.per_sub_question_scores if s["id"] == "1")
        sub_2 = next(s for s in r.per_sub_question_scores if s["id"] == "2")
        sub_3 = next(s for s in r.per_sub_question_scores if s["id"] == "3")
        assert sub_1["awarded"] == 5.0  # 1/1 coverage
        assert sub_2["awarded"] == 5.0
        # sub 3 漏答 → fallback 整段 → 整段去停用词含"A/B/内容" → 不含 "C" 关键词
        # 但 sub 3 kps=["C"], 单 char 被 token 匹配(按 _key_point_covered 规则)→ C 不在整段里
        assert sub_3["matched"] == 0  # 漏答 + 整段不含 C 关键词

    def test_case_analysis_total_cap_to_full_score(self):
        """rubric total points > full_score → score cap 到 full_score。"""
        # rubric 总分 20 但题目 full=10 → 应 cap 到 10
        rubric_over = {
            "sub_questions": [{"id": "1", "points": 15, "key_points": ["x"]}],
            "conclusion": {"points": 5, "criteria": ["y"]},
        }
        r = _grade_case_analysis(
            user_answer="1.x y", rubric=rubric_over, full_score=10.0,
        )
        assert r.awarded_score == 10.0  # capped from 20


class TestParseSubAnswersToMap:
    """parse_sub_answers_to_map 独立单测。"""

    def test_sub_id_position_mapping_numbered(self):
        """按编号+分号切分 → 按位置 map 到 sub_question ids。"""
        # 学员用 ';' 分隔 3 段(parse_sub_answers 切 3 段)
        user = "1.A内容;2.B内容;3.C内容"
        rubric = {
            "sub_questions": [
                {"id": "1", "points": 3, "key_points": []},
                {"id": "2", "points": 4, "key_points": []},
                {"id": "3", "points": 3, "key_points": []},
            ]
        }
        out = parse_sub_answers_to_map(user, rubric)
        assert set(out.keys()) == {"1", "2", "3"}
        assert "A内容" in out["1"]
        assert "B内容" in out["2"]
        assert "C内容" in out["3"]

    def test_sub_id_no_separator_falls_back_to_full_text(self):
        """无可识别分隔(无分号/编号) → 整段对所有 sub(精度 fallback)。"""
        user = "整段答案包含三个子问题核心内容但未用分号切"
        rubric = {
            "sub_questions": [
                {"id": "1", "points": 3, "key_points": []},
                {"id": "2", "points": 4, "key_points": []},
            ]
        }
        out = parse_sub_answers_to_map(user, rubric)
        # 单段 → 所有 sub 拿同一份整段(无 false-positive 切分)
        assert out["1"] == user
        assert out["2"] == user

    def test_no_rubric_returns_empty(self):
        """rubric=None → {}。"""
        out = parse_sub_answers_to_map("1.A;2.B", None)
        assert out == {}

    def test_user_answer_too_short_returns_empty(self):
        """用户答案过短(parse_sub_answers 返回 []) → {}。"""
        rubric = {"sub_questions": [{"id": "1"}]}
        out = parse_sub_answers_to_map("短", rubric)
        assert out == {}


# ============================================================================
# fix-30 P0:grade_answer dispatcher 路由 7 题型 参数化
# ============================================================================


@pytest.mark.parametrize(
    "q_type, expect_func_name",
    [
        ("single", "_grade_objective"),
        ("multi", "_grade_objective"),
        ("judge", "_grade_objective"),
        ("calc", "_grade_subjective"),
        ("comprehensive", "_grade_subjective"),
        ("short_answer", "_grade_short_answer"),
        ("case_analysis", "_grade_case_analysis"),
    ],
)
def test_grade_answer_dispatcher_routes_to_correct_helper(q_type, expect_func_name, monkeypatch):
    """每种 q_type 调对应的内部 helper(spy 实现行为)。"""
    calls = []

    if expect_func_name == "_grade_objective":
        def fake_obj(qt, ca, ua, fs):
            calls.append((qt, ca, ua, fs))
            from app.services.grader import GradedAnswer
            return GradedAnswer(awarded_score=fs, is_correct=True, comment="ok")
        monkeypatch.setattr("app.services.grader._grade_objective", fake_obj)
        r = grade_answer(q_type, "A", "A", 2.0)
        assert calls == [(q_type, "A", "A", 2.0)]
        assert r.awarded_score == 2.0
    elif expect_func_name == "_grade_subjective":
        def fake_subj(correct_answer, user_answer, full_score, key_points, min_coverage):
            calls.append((correct_answer, user_answer, full_score, key_points))
            from app.services.grader import GradedAnswer
            return GradedAnswer(awarded_score=full_score, is_correct=True, comment="ok")
        monkeypatch.setattr("app.services.grader._grade_subjective", fake_subj)
        r = grade_answer(q_type, "ref", "user answer", 5.0, key_points=["x"])
        assert calls and calls[0][2] == 5.0
        assert calls[0][3] == ["x"]
    elif expect_func_name == "_grade_short_answer":
        def fake_sa(correct_answer, user_answer, full_score, key_points, min_coverage):
            calls.append((correct_answer, user_answer, full_score, key_points))
            from app.services.grader import GradedAnswer
            return GradedAnswer(awarded_score=full_score, is_correct=True, comment="ok")
        monkeypatch.setattr("app.services.grader._grade_short_answer", fake_sa)
        r = grade_answer(q_type, "ref", "user short", 3.0, key_points=["x"])
        assert r.awarded_score == 3.0
    elif expect_func_name == "_grade_case_analysis":
        def fake_ca(user_answer, rubric, full_score, min_coverage=0.6):
            calls.append((user_answer, rubric, full_score))
            from app.services.grader import GradedAnswer
            return GradedAnswer(awarded_score=full_score, is_correct=True, comment="ok",
                                per_sub_question_scores=[])
        monkeypatch.setattr("app.services.grader._grade_case_analysis", fake_ca)
        rubric = {"sub_questions": [{"id": "1"}], "conclusion": None}
        r = grade_answer(q_type, "ref", "user", 10.0, rubric=rubric)
        assert r.awarded_score == 10.0
        assert calls[0][1] == rubric


# ============================================================================
# 回归保护(回归 #1 — adapted payload 闭环)
# ============================================================================


class TestAdaptedPayloadRegression:
    """fix-25 闭环 regression:adapted 题目用 adapted_answer 判分;

    grader 自身不应给 key_points 注入变化;只 verified correct_answer 参数本身。
    这里证:_grade_subjective 在 user 答 'ZZZZ' 时一致给 0(不依赖 adapted)。
    """

    def test_z_user_answer_returns_zero(self):
        """学员全部答'ZZZZ' → 0 分(不应被 grader 误判为部分覆盖)。"""
        kps = ["战略分析", "竞争优势", "执行方案"]
        # 模拟被混入一些高分干扰词(如"ZZZZ答案的key points")
        r = _grade_subjective(
            correct_answer="参考答案",
            user_answer="ZZZZ答案完全无关",
            full_score=5.0,
            key_points=kps,
            min_coverage=0.6,
        )
        # 'ZZZZ' / '答案' / '完全' / '无关' 都不在 key_points → 0 分
        assert r.awarded_score == 0.0
        # 即使有"答案"两字,如未覆盖 key_points 全部 3 个,不会到 60% threshold
        assert r.is_correct is False

    def test_z_user_then_partial_keyword_match(self):
        """仅含一个 key_point(33%) < 60% → 0 分(不是 1 分)。

        Phase 2-Lane-B:comment 改 wrong 池化("1/3"/"未达" 不再硬编码)。
        """
        _reset_praise_history()
        kps = ["战略选择", "竞争优势", "执行方案"]
        r = _grade_subjective(
            correct_answer="ref",
            user_answer="这个案例应做战略选择。",
            full_score=5.0,
            key_points=kps,
            min_coverage=0.6,
        )
        # 1/3 = 33% < 60% → 0
        assert r.awarded_score == 0.0
        _assert_praise_comment(r, "wrong")


# ============================================================================
# 回归保护(回归 #2 — mixed mode 性能)
# ============================================================================


class TestMixedModePerformance:
    """保证 5 题型扩展不引入回归 — 单题判分 < 100ms (远低于 mixed mode 28s 启动预算)。

    spec §11 三档表:mixed worst case < 300s,单题判分仅是众多步骤之一。
    grader 端不应引入 O(N^2) 扫描或显著 overhead。
    """

    def test_single_grading_under_100ms(self):
        """单题判分(包括 calc/short_answer/case_analysis)在合理时间内。"""
        # Calc-like
        t0 = time.perf_counter()
        for _ in range(50):
            grade_answer(
                "calc", "ref", "用户答案" * 10,
                full_score=5.0,
                key_points=["战略选择", "竞争优势", "执行方案"],
            )
        calc_dur = time.perf_counter() - t0

        # Short answer
        t0 = time.perf_counter()
        for _ in range(50):
            grade_answer(
                "short_answer", "ref", "用户答案" * 5,
                full_score=3.0,
                key_points=["A", "B"],
            )
        sa_dur = time.perf_counter() - t0

        # Case analysis (rubric 解析 + multiple sub_questions)
        rubric = {
            "sub_questions": [
                {"id": str(i), "points": 3, "key_points": [f"kp{i}a", f"kp{i}b"]}
                for i in range(1, 6)
            ],
            "conclusion": {"points": 3, "criteria": ["end_a", "end_b"]},
        }
        t0 = time.perf_counter()
        for _ in range(20):
            grade_answer(
                "case_analysis", "ref",
                "1.kp1a kp1b；2.kp2a kp2b；3.kp3a kp3b；4.kp4a kp4b；5.kp5a kp5b。结论 end_a。",
                full_score=18.0,
                rubric=rubric,
            )
        ca_dur = time.perf_counter() - t0

        # 单题平均 < 100ms 宽松断言(50 题跑 5 秒以内)
        assert calc_dur < 5.0, f"calc 单题太慢: {calc_dur:.2f}s for 50 题"
        assert sa_dur < 5.0, f"short_answer 单题太慢: {sa_dur:.2f}s for 50 题"
        assert ca_dur < 5.0, f"case_analysis 单题太慢: {ca_dur:.2f}s for 20 题"


# ============================================================================
# 回归保护(回归 #3 — adapted 闭环同一题用原题答案应判对)
# ============================================================================


class TestCalcStillCorrectForValidAnswer:
    """保护 _grade_subjective 完全没被 5 题型扩展污染 — valid 答案仍满分。"""

    KEY_POINTS = ["债券价值", "面值", "市场利率", "票面利率", "现值系数"]

    def test_calc_full_coverage(self):
        ans = "债券价值=面值×票面利率×现值系数，市场利率影响折现率"
        r = grade_answer("calc", "参考答案", ans, 5.0, key_points=self.KEY_POINTS)
        assert r.awarded_score == 5.0
        assert r.is_correct is True

    def test_comprehensive_full_coverage(self):
        """comprehensive 与 calc 走同一函数(回归保护)。"""
        ans = (
            "综合分析:本案例通过债券价值公式,"
            "用面值×票面利率计算各期现金流,"
            "市场利率6%,最后用现值系数折现求和"
        )
        r = grade_answer(
            "comprehensive", "参考答案", ans, 10.0,
            key_points=self.KEY_POINTS,  # 全 5 个 key_points 都覆盖
        )
        assert r.awarded_score == 10.0


# ============================================================================
# fix-30 P0:GradedAnswer.per_sub_question_scores 字段类型
# ============================================================================


def test_per_sub_question_scores_field_type():
    """case_analysis 题型返回 per_sub_question_scores 是 list[dict];其它题型为 None。"""
    rubric = {
        "sub_questions": [{"id": "1", "points": 5, "key_points": ["A"]}],
        "conclusion": None,
    }
    r = grade_answer(
        "case_analysis", "ref", "1.A.", full_score=5.0, rubric=rubric,
    )
    assert isinstance(r.per_sub_question_scores, list)
    assert all(isinstance(s, dict) for s in r.per_sub_question_scores)
    # 内部 schema keys(per_sub_question_scores[i] 字段):
    expected_keys = {"id", "points", "awarded", "matched", "total", "coverage", "missed_points"}
    for s in r.per_sub_question_scores:
        assert expected_keys.issubset(s.keys())

    # calc 题型 per_sub_question_scores 为 None
    r_calc = grade_answer(
        "calc", "ref", "完整覆盖: 债券价值", 5.0,
        key_points=["债券价值"],
    )
    assert r_calc.per_sub_question_scores is None

    # single/multi/judge 同上
    for qt in ("single", "multi", "judge"):
        r_obj = grade_answer(qt, "A", "A", 2.0)
        assert r_obj.per_sub_question_scores is None, qt
"""判分器测试 — 客观题矩阵 + 主观题阈值边界 + sub_answer 拆解（fix-17）。"""
from __future__ import annotations

import pytest

from app.services.grader import grade_answer, parse_key_points, parse_sub_answers


# ---------- 客观题矩阵 ----------


class TestSingleChoice:
    """单选题边界。"""

    def test_correct(self):
        r = grade_answer("single", "A", "A", 2.0)
        assert r.awarded_score == 2.0
        assert r.is_correct is True
        assert r.comment == "回答正确"

    def test_wrong(self):
        r = grade_answer("single", "A", "B", 2.0)
        assert r.awarded_score == 0.0
        assert r.is_correct is False
        assert "A" in r.comment

    def test_empty(self):
        r = grade_answer("single", "A", "", 2.0)
        assert r.awarded_score == 0.0
        assert "未作答" in r.comment

    def test_none(self):
        r = grade_answer("single", "A", None, 2.0)
        assert r.awarded_score == 0.0
        assert "未作答" in r.comment


class TestMultiChoice:
    """多选题 — 漏选/多选/错选 都应判错。"""

    def test_full_correct(self):
        r = grade_answer("multi", "ABD", "ABD", 3.0)
        assert r.awarded_score == 3.0
        assert r.is_correct is True

    def test_correct_with_separator(self):
        # 学员用逗号分隔
        r = grade_answer("multi", "ABD", "A,B,D", 3.0)
        assert r.awarded_score == 3.0
        assert r.is_correct is True

    def test_missing_one(self):
        # 漏选 B → 错
        r = grade_answer("multi", "ABD", "AD", 3.0)
        assert r.awarded_score == 0.0
        assert r.is_correct is False

    def test_extra_one(self):
        # 多选 C → 错
        r = grade_answer("multi", "ABD", "ABCD", 3.0)
        assert r.awarded_score == 0.0
        assert r.is_correct is False

    def test_empty(self):
        r = grade_answer("multi", "ABD", "", 3.0)
        assert r.awarded_score == 0.0


class TestJudge:
    """判断题 — 文本答案比对（对/错）。"""

    def test_correct(self):
        r = grade_answer("judge", "对", "对", 1.0)
        assert r.awarded_score == 1.0
        assert r.is_correct is True

    def test_wrong(self):
        r = grade_answer("judge", "对", "错", 1.0)
        assert r.awarded_score == 0.0
        assert r.is_correct is False


# ---------- 主观题阈值边界 ----------


class TestSubjectiveThresholds:
    """主观题关键词覆盖率阈值边界。"""

    KEY_POINTS = ["债券价值", "面值", "市场利率", "票面利率", "现值系数"]

    def test_empty_answer(self):
        r = grade_answer("calc", "参考答案", "", 5.0, key_points=self.KEY_POINTS)
        assert r.awarded_score == 0.0
        assert "未作答" in r.comment

    def test_short_answer_below_threshold(self):
        # < 5 字 → 0 分
        r = grade_answer("calc", "参考答案", "对", 5.0, key_points=self.KEY_POINTS)
        assert r.awarded_score == 0.0
        assert "过短" in r.comment

    def test_full_coverage(self):
        # 全部关键点都覆盖 → 满分
        ans = "债券价值=面值×票面利率×现值系数，市场利率影响折现率"
        r = grade_answer("calc", "参考答案", ans, 5.0, key_points=self.KEY_POINTS)
        assert r.awarded_score == 5.0
        assert r.is_correct is True
        assert "完整覆盖" in r.comment

    def test_just_below_threshold(self):
        # 4/5 = 80% — 介于 0.6 和 1.0 之间
        ans = "债券价值=面值×票面利率×现值系数"  # 缺"市场利率"
        r = grade_answer("calc", "参考答案", ans, 5.0, key_points=self.KEY_POINTS)
        assert r.awarded_score == 4.0  # 80% × 5
        assert r.is_correct is True
        assert "覆盖 4/5" in r.comment

    def test_exact_threshold(self):
        # 3/5 = 60% — 刚好达门槛
        ans = "债券价值公式中含面值与现值系数两个关键变量"  # 含 债券价值, 面值, 现值系数
        r = grade_answer("calc", "参考答案", ans, 5.0, key_points=self.KEY_POINTS)
        assert r.awarded_score == 3.0  # 60% × 5
        assert r.is_correct is True

    def test_below_threshold(self):
        # 2/5 = 40% < 60% → 0 分
        ans = "债券价值通过现值系数计算"
        r = grade_answer("calc", "参考答案", ans, 5.0, key_points=self.KEY_POINTS)
        assert r.awarded_score == 0.0
        assert "未达 60% 门槛" in r.comment

    def test_zero_coverage(self):
        ans = "这个答案与题目完全没有关系，纯粹瞎编一些内容凑字数"
        r = grade_answer("calc", "参考答案", ans, 5.0, key_points=self.KEY_POINTS)
        assert r.awarded_score == 0.0

    def test_empty_key_points_fallback(self):
        # key_points 缺失 → 退化为参考答案完全匹配
        ans = "参考答案文本"
        r = grade_answer("calc", "参考答案文本", ans, 5.0, key_points=None)
        assert r.awarded_score == 5.0
        assert r.is_correct is True

    def test_empty_key_points_mismatch(self):
        # ≥ 5 字才进入 key_points 退化分支
        r = grade_answer("calc", "参考答案", "学员给的答案文本", 5.0, key_points=None)
        assert r.awarded_score == 0.0
        assert "关键要点缺失" in r.comment


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
    """主观题结合 sub_answers 拆解判分（fix-17）。"""

    def test_numbered_full_coverage(self):
        """编号格式 + 完整覆盖 key_points → 满分 + 评语含"识别到 3 个分小问"。"""
        kps = ["年金现值系数", "终值系数", "折现率"]
        user_answer = "1.年金现值系数0.95；2.终值系数1.05；3.折现率5%"
        r = grade_answer("calc", "参考答案", user_answer, 5.0, key_points=kps)
        assert r.awarded_score == 5.0
        assert r.is_correct is True
        assert "完整覆盖" in r.comment
        assert "3/3" in r.comment
        assert "识别到 3 个分小问作答" in r.comment
        assert r.sub_answer_count == 3
        assert r.missed_points is None  # 满分无未覆盖

    def test_numbered_partial_coverage(self):
        """编号格式 + 覆盖 2/3 → 部分给分 + 列出未覆盖。"""
        kps = ["年金现值系数", "终值系数", "折现率"]
        user_answer = "1.年金现值系数0.95；2.终值系数1.05"  # 缺"折现率"
        r = grade_answer("calc", "参考答案", user_answer, 5.0, key_points=kps)
        # coverage = 2/3 ≈ 0.67 ≥ 0.6 → 部分给分
        assert 0 < r.awarded_score < 5.0
        assert r.is_correct is True
        assert "覆盖 2/3" in r.comment
        assert "识别到 2 个分小问作答" in r.comment
        assert "未覆盖" in r.comment
        assert "折现率" in r.comment  # 未覆盖的 key_point
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
        assert "完整覆盖" in r.comment
        assert r.sub_answer_count is None  # 无编号 → 不显示分小问数

    def test_semicolon_only_full_coverage(self):
        """分号分隔 + 完整覆盖 → 满分 + 识别到 N 个分小问。"""
        kps = ["现值", "终值"]
        user_answer = "现值1000；终值1050"
        r = grade_answer("calc", "参考答案", user_answer, 5.0, key_points=kps)
        assert r.awarded_score == 5.0
        assert "识别到 2 个分小问作答" in r.comment

    def test_missed_points_truncation(self):
        """未覆盖要点超过 80 字 → 截断 + 省略号。"""
        # 构造足够长的 key_points 触发截断
        kps = [
            "超长关键要点A" * 5,
            "超长关键要点B" * 5,
            "超长关键要点C" * 5,
            "短的D",
        ]
        user_answer = "1.短的D内容"  # 只覆盖 D
        r = grade_answer("calc", "参考答案", user_answer, 5.0, key_points=kps)
        # coverage = 1/4 = 0.25 < 0.6 → score=0
        # 但 Option A 仍追加 missed_points
        assert r.awarded_score == 0.0
        if r.missed_points is not None:
            # 若追加了 missed_points，comment 末尾应是截断后的 80 字
            assert "未覆盖" in r.comment
            # 截断后 ≤ 81 字（含 "…"）
            tail = r.comment.split("未覆盖：")[-1]
            assert len(tail) <= 81

    def test_short_answer_returns_zero(self):
        """< 5 字 → 0 分（不变行为）。"""
        kps = ["A", "B", "C"]
        r = grade_answer("calc", "x", "短", 5.0, key_points=kps)
        assert r.awarded_score == 0.0
        assert "过短" in r.comment
        assert r.sub_answer_count is None
        assert r.missed_points is None
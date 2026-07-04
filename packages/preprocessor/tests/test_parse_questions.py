"""parse_questions 模块的单元测试。

跑法：
    cd finance-exam-system
    python -m packages.preprocessor.tests.test_parse_questions
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

# 让 ``from packages.preprocessor...`` 可导入
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from packages.preprocessor.parse_questions import (
    Question,
    _compute_id,
    _clean_answer_for_objective,
    _extract_key_points,
    detect_chapter,
    _type_label_to_enum,
)


class TestSchema(unittest.TestCase):
    """Pydantic schema 严格性。"""

    def test_extra_field_forbidden(self):
        """任何未声明字段必须拒绝。"""
        with self.assertRaises(Exception):
            Question(
                id="x" * 16,
                type="single",
                chapter="ch1",
                number=1,
                stem="x",
                options=["A、a", "B、b", "C、c", "D、d"],
                answer="A",
                source_pdf="x.pdf",
                page_ref=1,
                rogue="unexpected",  # type: ignore[call-arg]
            )

    def test_difficulty_must_be_none(self):
        """开发期 difficulty 必须为 null（阶段 ②.5 由 DeepSeek 填充）。"""
        with self.assertRaises(Exception):
            Question(
                id="x" * 16,
                type="single",
                chapter="ch1",
                number=1,
                stem="x",
                options=["A、a", "B、b", "C、c", "D、d"],
                answer="A",
                difficulty=2,  # type: ignore[arg-type]
                source_pdf="x.pdf",
                page_ref=1,
            )


class TestHelpers(unittest.TestCase):
    """纯函数单元测试。"""

    def test_compute_id_is_stable(self):
        """同一题应产生相同 id。"""
        a = _compute_id("ch1", 1, "single", "题干内容...", "第一章.pdf")
        b = _compute_id("ch1", 1, "single", "题干内容...", "第一章.pdf")
        self.assertEqual(a, b)

    def test_compute_id_differs_by_stem(self):
        """题干不同则 id 不同。"""
        a = _compute_id("ch1", 1, "single", "题干A", "x.pdf")
        b = _compute_id("ch1", 1, "single", "题干B", "x.pdf")
        self.assertNotEqual(a, b)

    def test_clean_answer_single(self):
        # single: 提取首个 A-D 字母
        self.assertEqual(_clean_answer_for_objective("single", "A", None), "A")
        self.assertEqual(_clean_answer_for_objective("single", "A.", None), "A")
        self.assertEqual(_clean_answer_for_objective("single", "B", None), "B")

    def test_clean_answer_multi(self):
        # multi: 提取首段连续字母（避免 PAGE 误匹配）
        self.assertEqual(
            _clean_answer_for_objective("multi", "ABCD", ["A", "B", "C", "D"]),
            "ABCD",
        )
        # 不连续时取首段
        self.assertEqual(
            _clean_answer_for_objective("multi", "AB text CD", ["A", "B", "C", "D"]),
            "AB",
        )

    def test_clean_answer_judge(self):
        self.assertEqual(_clean_answer_for_objective("judge", "对", None), "对")
        self.assertEqual(_clean_answer_for_objective("judge", "错", None), "错")

    def test_type_label_to_enum(self):
        self.assertEqual(_type_label_to_enum("单选题"), "single")
        self.assertEqual(_type_label_to_enum("多选题"), "multi")
        self.assertEqual(_type_label_to_enum("判断题"), "judge")
        self.assertEqual(_type_label_to_enum("填空题"), "calc")
        self.assertEqual(_type_label_to_enum("计算分析题"), "calc")
        self.assertEqual(_type_label_to_enum("综合题"), "comprehensive")

    def test_detect_chapter_from_filename(self):
        self.assertEqual(detect_chapter("第七章(1).pdf", ""), "ch7")
        self.assertEqual(detect_chapter("第一章课后练习(1)(1).pdf", ""), "ch1")
        self.assertEqual(detect_chapter("第九章 即测即评(1)(1).pdf", ""), "ch9")

    def test_extract_key_points_knowledge_block(self):
        points = _extract_key_points(
            "2.1.1 货币时间价值\n2.1.2 一次性收付款项",
            None,
        )
        self.assertGreaterEqual(len(points), 2)
        self.assertIn("货币时间价值", points)

    def test_extract_key_points_fallback(self):
        """当无知识点时，从短解析中提取。"""
        points = _extract_key_points(
            None,
            "甲公司的资本成本计算。乙公司的成本较低。丙公司较高。丁公司更低。",
        )
        self.assertGreaterEqual(len(points), 2)


class TestEndToEnd(unittest.TestCase):
    """端到端测试：解析真实 PDF 并校验输出。"""

    @classmethod
    def setUpClass(cls):
        from packages.preprocessor import parse_questions

        cls.parse_questions = parse_questions
        # __file__ = .../packages/preprocessor/tests/test_parse_questions.py
        # parents[3] = 项目根 EggEgg_Examination_System
        cls.PROJECT_ROOT = Path(__file__).resolve().parents[3]
        cls.PDF_DIR = cls.PROJECT_ROOT / "财务管理资料"
        cls.PARSED_DIR = cls.PROJECT_ROOT / "data" / "parsed"

    def test_questions_jsonl_exists_and_passes_pydantic(self):
        """data/parsed/questions.jsonl 必须存在且全部通过 Pydantic。"""
        path = self.PARSED_DIR / "questions.jsonl"
        self.assertTrue(path.exists(), f"{path} not found")
        with open(path, encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                data = json.loads(line)
                try:
                    Question(**data)
                except Exception as e:
                    self.fail(f"L{line_no} 校验失败: {e}")

    def test_total_count_at_least_500(self):
        """总题数应不少于 500（说明 12 个 PDF 都被处理）。"""
        path = self.PARSED_DIR / "questions.jsonl"
        with open(path, encoding="utf-8") as f:
            n = sum(1 for _ in f)
        self.assertGreaterEqual(n, 500, f"题数过少: {n}")

    def test_all_chapters_present(self):
        """ch1~ch9 都应有题目。"""
        path = self.PARSED_DIR / "questions.jsonl"
        chapters = set()
        with open(path, encoding="utf-8") as f:
            for line in f:
                chapters.add(json.loads(line)["chapter"])
        for ch in {f"ch{i}" for i in range(1, 10)}:
            self.assertIn(ch, chapters, f"{ch} 缺失")

    def test_all_types_present(self):
        """single/multi/judge/calc 四种题型都应有。"""
        path = self.PARSED_DIR / "questions.jsonl"
        types = set()
        with open(path, encoding="utf-8") as f:
            for line in f:
                types.add(json.loads(line)["type"])
        for t in {"single", "multi", "judge", "calc"}:
            self.assertIn(t, types, f"{t} 缺失")

    def test_judge_options_canonical(self):
        """所有判断题 options 必须严格等于 ['对', '错']。"""
        path = self.PARSED_DIR / "questions.jsonl"
        with open(path, encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                q = json.loads(line)
                if q["type"] == "judge":
                    self.assertEqual(
                        q["options"], ["对", "错"],
                        f"L{line_no} judge options 异常: {q['options']}"
                    )

    def test_objective_answer_in_options(self):
        """客观题答案必须在 options 中。"""
        path = self.PARSED_DIR / "questions.jsonl"
        with open(path, encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                q = json.loads(line)
                if q["type"] == "single" and q["options"]:
                    letters = {o[0] for o in q["options"]}
                    self.assertIn(
                        q["answer"], letters,
                        f"L{line_no} 答案 {q['answer']!r} 不在 {letters}"
                    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
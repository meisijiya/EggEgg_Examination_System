"""Pydantic schema 单元测试 — fix-23a P0 critical。

覆盖:
- `GradedAnswerDetail.options` 字段(必填 schema 存在性)
- `StartExamRequest.subject_id` 必填 + `mode` 默认值
- `StartExamRequest` 严格模式(extra=forbid)
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas import GradedAnswerDetail, StartExamRequest


class TestStartExamRequest:
    """StartExamRequest schema 校验(fix-23a)。"""

    def test_subject_id_required(self):
        """subject_id 缺失 → ValidationError。"""
        with pytest.raises(ValidationError) as exc:
            StartExamRequest()
        errors = exc.value.errors()
        assert any("subject_id" in str(e) for e in errors)

    def test_subject_id_required_mode_only_rejected(self):
        """只传 mode 不传 subject_id → ValidationError(fix-23a 强制要求)。"""
        with pytest.raises(ValidationError) as exc:
            StartExamRequest(mode="standard")
        assert "subject_id" in str(exc.value)

    def test_subject_id_string_accepted(self):
        """subject_id 是字符串 → 接受。"""
        req = StartExamRequest(subject_id="fin-mgmt")
        assert req.subject_id == "fin-mgmt"
        assert req.mode == "standard"  # 默认值

    def test_mode_explicit_value(self):
        """显式传 mode → 保留。"""
        req = StartExamRequest(subject_id="fin-mgmt", mode="mixed")
        assert req.mode == "mixed"

    def test_extra_fields_forbidden(self):
        """未声明字段 → ValidationError(extra=forbid)。"""
        with pytest.raises(ValidationError):
            StartExamRequest(subject_id="fin-mgmt", unknown="x")

    def test_invalid_mode_rejected(self):
        """非法 mode 字面量 → ValidationError。"""
        with pytest.raises(ValidationError):
            StartExamRequest(subject_id="fin-mgmt", mode="invalid-mode")

    def test_empty_subject_id_rejected(self):
        """空字符串 subject_id → ValidationError(min_length=1)。"""
        with pytest.raises(ValidationError):
            StartExamRequest(subject_id="")


class TestGradedAnswerDetailOptions:
    """GradedAnswerDetail.options 字段(fix-23a)。"""

    def _make_minimal(self, **overrides) -> GradedAnswerDetail:
        """构造一个最小可用的 GradedAnswerDetail。"""
        base = {
            "question_id": 1,
            "sequence": 1,
            "type": "single",
            "chapter_code": "ch1",
            "stem": "Q stem",
            "user_answer": "A",
            "correct_answer": "A",
            "is_correct": True,
            "awarded_score": 2.0,
            "full_score": 2.0,
            "comment": "正确",
        }
        base.update(overrides)
        return GradedAnswerDetail(**base)

    def test_options_field_exists_default_none(self):
        """options 字段存在,默认 None。"""
        d = self._make_minimal()
        assert hasattr(d, "options"), "GradedAnswerDetail 必须含 options 字段"
        assert d.options is None

    def test_options_accepts_list_of_strings(self):
        """options 接受字符串列表。"""
        opts = ["选项1", "选项2", "选项3", "选项4"]
        d = self._make_minimal(options=opts)
        assert d.options == opts

    def test_options_accepts_none_explicitly(self):
        """显式传 None 也接受(主观题/计算题/简答题/案例分析)。"""
        d = self._make_minimal(options=None)
        assert d.options is None

    def test_options_serialized_to_dict(self):
        """model_dump 序列化时 options 字段出现。"""
        d = self._make_minimal(options=["A", "B", "C"])
        out = d.model_dump()
        assert "options" in out
        assert out["options"] == ["A", "B", "C"]

    def test_options_not_serialized_when_none_when_excluded(self):
        """options=None 仍出现在序列化结果里(不排除)。"""
        d = self._make_minimal()
        out = d.model_dump()
        # 显式 None 也应在 model_dump 出现(默认不 exclude_none)
        assert "options" in out
        assert out["options"] is None

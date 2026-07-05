"""Phase 5 fix-6 — parse_docx GFM markdown table 测试。

fix-30 核心变更：
- _iter_table_texts 把 <w:tbl> 拍平成 ' | '.join 单字符串 → 改 emit GFM markdown table
- _format_markdown_table: header + |---| separator + body rows
- 多余列截断 / 缺列补空 — 容错边界

不依赖真实 DOCX：用 SimpleNamespace-like mock 构造 w:tbl-like 对象 + sys.modules 注入 mock docx。
（ponytail: 避开 python-docx 依赖，MagicMock 对 `.strip()` 链式调用会污染输出，用纯 object + 属性更可控）
"""
from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest


class _MockCell:
    """替代 MagicMock — 显式存 .text 属性,避免链式 MagicMock 污染。"""
    def __init__(self, text: str) -> None:
        self.paragraphs = [_MockPara(text)]


class _MockPara:
    def __init__(self, text: str) -> None:
        self.text = text


class _MockRow:
    def __init__(self, cells_text: list[str]) -> None:
        self.cells = [_MockCell(t) for t in cells_text]


class _MockTable:
    def __init__(self, rows: list[list[str]]) -> None:
        self.rows = [_MockRow(r) for r in rows]


class _FakeDocument:
    """替代 docx.Document — 构造时绑定 tables 列表。"""
    def __init__(self, _path: str, tables: list[_MockTable] | None = None) -> None:
        self.tables = tables or []


def _install_fake_docx(tables: list[list[list[str]]]):
    """在 sys.modules['docx'] 注入 fake module,带预定义的 tables。"""
    mod = types.ModuleType("docx_fake")
    fake_tables = [_MockTable(t) for t in tables]
    mod.Document = lambda path: _FakeDocument(path, fake_tables)  # type: ignore[attr-defined]
    sys.modules["docx"] = mod


def _reload_parse_docx():
    """重新 import parse_docx — 让其内部 `from docx import Document` 复用我们注入的 fake。"""
    sys.modules.pop("packages.preprocessor.parse_docx", None)
    return importlib.import_module("packages.preprocessor.parse_docx")


# ---------------------------------------------------------------------------
# _format_markdown_table 单元测试 (无 mock 需求)
# ---------------------------------------------------------------------------


class TestFormatMarkdownTable:
    """2D cells → GFM markdown table 行为校验。"""

    def test_format_markdown_table_with_header_and_body(self):
        rows = [
            ["资产", "期末数"],
            ["货币资金", "1000"],
            ["应收账款", "2000"],
        ]
        mod = _reload_parse_docx()
        out = mod._format_markdown_table(rows)
        # 3 行入参 → 4 行输出（header + sep + body 1 + body 2）
        assert out.split("\n") == [
            "| 资产 | 期末数 |",
            "| --- | --- |",
            "| 货币资金 | 1000 |",
            "| 应收账款 | 2000 |",
        ]

    def test_format_markdown_table_single_row(self):
        """1 行表只输出 header + separator，无 body 行（ponytail: minimal valid GFM）。"""
        mod = _reload_parse_docx()
        out = mod._format_markdown_table([["col1", "col2"]])
        assert out == "| col1 | col2 |\n| --- | --- |"

    def test_format_markdown_table_empty(self):
        mod = _reload_parse_docx()
        assert mod._format_markdown_table([]) == ""

    def test_format_markdown_table_normalizes_short_rows(self):
        """body 行缺列 → 补空字符串。"""
        mod = _reload_parse_docx()
        out = mod._format_markdown_table([["A", "B", "C"], ["1"]])  # body 只有 1 列
        lines = out.split("\n")
        assert lines[0] == "| A | B | C |"
        assert lines[1] == "| --- | --- | --- |"
        assert lines[2] == "| 1 |  |  |"

    def test_format_markdown_table_truncates_long_rows(self):
        """body 行多列 → 截断到 header 列数。"""
        mod = _reload_parse_docx()
        out = mod._format_markdown_table([["A", "B"], ["1", "2", "3", "4"]])
        assert "| 1 | 2 |" in out
        assert "| 3 | 4" not in out


# ---------------------------------------------------------------------------
# _iter_table_texts 行为校验（用 sys.modules fake docx 避免 I/O）
# ---------------------------------------------------------------------------


class TestIterTableTextsGFM:
    """验证 _iter_table_texts emit GFM markdown table 而非旧版 ' | '.join 拍平。"""

    def _call(self, tables: list[list[list[str]]], tmp_path: Path):
        _install_fake_docx(tables)
        try:
            mod = _reload_parse_docx()
            fake_docx = tmp_path / "fake.docx"
            fake_docx.write_text("")
            return mod._iter_table_texts(fake_docx)
        finally:
            sys.modules.pop("docx", None)
            sys.modules.pop("packages.preprocessor.parse_docx", None)

    def test_iter_table_texts_returns_2d_normalized_to_markdown(self, tmp_path: Path):
        """含 1 个 2x2 表格的 mock doc → 返回 GFM markdown table 而非拍平串。"""
        result = self._call(
            [
                [
                    ["资产", "金额"],
                    ["现金", "1000"],
                ]
            ],
            tmp_path,
        )
        assert len(result) == 1
        idx, text = result[0]
        # 索引从 100_000 起
        assert idx == 100_000
        # GFM 关键 — 必须有 |---| separator
        assert "| --- | --- |" in text
        # 必须有 header 行
        assert "| 资产 | 金额 |" in text
        # 必须有 body 行
        assert "| 现金 | 1000 |" in text
        # 旧版 bug 形态: ' | '.join 拍平 → 单行多个 ' | '
        #   markdown 表格应是 3 行结构: header + sep + body
        assert text.count("\n") == 2

    def test_iter_table_texts_no_tables(self, tmp_path: Path):
        """无表的 doc → 返回空 list。"""
        result = self._call([], tmp_path)
        assert result == []

    def test_iter_table_texts_multiple_tables_increment_index(self, tmp_path: Path):
        """多个 table → 索引 +1 起步。"""
        result = self._call(
            [
                [["h"]],
                [["a"]],
            ],
            tmp_path,
        )
        assert [t[0] for t in result] == [100_000, 100_001]
        # 每个都返回 GFM 表格（header + sep）
        for _, text in result:
            assert "| --- |" in text

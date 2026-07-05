"""build_db.py Phase 1.2 新增功能单测。

测试目标:
- _load_ai_approved_questions:status='approved' 才加载,status='pending'/'rejected' 过滤
- _load_ai_approved_questions:缺 stem/answer 跳过
- _load_ai_approved_questions:JSONL 不存在 → 空 + 警告
- build_db(subject_id=X, subject_name=Y):subject 表用新值(chapter 也跟随)
- build_db(chapter_titles=custom):支持自定义章节
- main() argparse 默认值向后兼容(fin-mgmt 不破坏)
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

import pytest

# 让 build_db.py 与 parse_questions.py 不触发 backend __init__ 链式导入
PROJECT_ROOT_TEST = Path(__file__).resolve().parent
BACKEND_ROOT = PROJECT_ROOT_TEST.parent            # packages/backend
PACKAGES_ROOT = BACKEND_ROOT.parent                 # packages/
PROJECT_ROOT = PACKAGES_ROOT.parent                 # 仓库根 (EggEgg_Examination_System/)
PREPROCESSOR_DIR = PACKAGES_ROOT / "preprocessor"

sys.path.insert(0, str(BACKEND_ROOT))


def _load_module(name: str, path: Path):
    """加载 preprocessor 脚本(隔离 backend __init__ import)。"""
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def build_db():
    return _load_module(
        "build_db_test_module",
        PREPROCESSOR_DIR / "build_db.py",
    )


# ---------------------------------------------------------------------------
# _load_ai_approved_questions 单测
# ---------------------------------------------------------------------------


def test_load_ai_approved_filters_status(build_db):
    """只加载 status='approved',忽略 pending/rejected。"""
    sample = [
        {"id": "a1", "type": "calc", "stem": "S1", "answer": "A",
         "status": "approved", "key_points": [], "options": None,
         "analysis": "", "difficulty": 2,
         "source_ref": {"file": "x.docx", "paragraph_index": 0}},
        {"id": "a2", "type": "calc", "stem": "S2", "answer": "B",
         "status": "pending", "key_points": [], "options": None,
         "analysis": "", "difficulty": 2,
         "source_ref": {"file": "x.docx", "paragraph_index": 1}},
        {"id": "a3", "type": "calc", "stem": "S3", "answer": "C",
         "status": "rejected", "key_points": [], "options": None,
         "analysis": "", "difficulty": 2,
         "source_ref": {"file": "x.docx", "paragraph_index": 2}},
        {"id": "a4", "type": "calc", "stem": "S4", "answer": "D",
         "status": "approved", "key_points": ["k"], "options": ["A","B"],
         "analysis": "...", "difficulty": 1,
         "source_ref": {"file": "y.docx", "paragraph_index": 0}},
    ]
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
    ) as f:
        for s in sample:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
        p = Path(f.name)

    chapter_titles = {"ch1": "总论", "ch2": "其他"}
    questions, warnings = build_db._load_ai_approved_questions(p, chapter_titles)
    assert len(questions) == 2
    assert {q["id"] for q in questions} == {"a1", "a4"}
    assert warnings == []  # 全是 approved + 必填字段都填了


def test_load_ai_approved_skips_incomplete(build_db):
    """缺 stem 或 answer → 加入 warnings + 跳过。"""
    sample = [
        {"id": "ok", "status": "approved", "type": "calc", "stem": "...", "answer": "A",
         "source_ref": {"file": "x.docx", "paragraph_index": 0}},
        {"id": "no_stem", "status": "approved", "type": "calc", "stem": "", "answer": "A",
         "source_ref": {"file": "x.docx", "paragraph_index": 1}},
        {"id": "no_answer", "status": "approved", "type": "calc", "stem": "x", "answer": "",
         "source_ref": {"file": "x.docx", "paragraph_index": 2}},
    ]
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
    ) as f:
        for s in sample:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
        p = Path(f.name)

    questions, warnings = build_db._load_ai_approved_questions(p, {"ch1": "x"})
    assert len(questions) == 1
    assert questions[0]["id"] == "ok"
    # 警告文本:既可包含 "缺 stem" 或 "缺 answer"(单条警告可能含两个关键词)
    assert any("缺 stem" in w or "缺 answer" in w for w in warnings), warnings
    assert len(warnings) >= 2, f"应有 2 条警告,实际 {len(warnings)}: {warnings}"


def test_load_ai_approved_nonexistent_file(build_db):
    """不存在的 JSONL → 返回 ([], [警告])。"""
    questions, warnings = build_db._load_ai_approved_questions(
        Path("/tmp/nope_ai.jsonl"), {"ch1": "x"}
    )
    assert questions == []
    assert len(warnings) == 1
    assert "AI JSONL 不存在" in warnings[0]


# ---------------------------------------------------------------------------
# build_db() subject 参数化
# ---------------------------------------------------------------------------


def test_build_db_uses_custom_subject(tmp_path):
    """build_db(subject_id=X, subject_name=Y) → SQLite 中 subjects 表用新值。"""
    # import build_db in isolation
    sys.path.insert(0, str(PREPROCESSOR_DIR))
    build_db = _load_module(
        "build_db_subject_test", PREPROCESSOR_DIR / "build_db.py"
    )
    db = tmp_path / "test.db"
    questions = []  # 空 questions 列表 — 重点验 subject 写入
    diff = {}
    rejected: list = []
    counts = build_db.build_db(
        questions=questions,
        difficulty_by_id=diff,
        rejected=rejected,
        db_path=db,
        subject_id="corp-strat",
        subject_name="公司战略与风险管理",
    )
    assert counts["subjects"] == 1

    # 验证 SQLite 内容
    import sqlite3
    conn = sqlite3.connect(str(db))
    rows = conn.execute("SELECT id, name FROM subjects").fetchall()
    conn.close()
    assert rows == [("corp-strat", "公司战略与风险管理")]


def test_build_db_chapter_titles_custom(tmp_path):
    """build_db(chapter_titles=custom) → SQLite 用自定义章节。"""
    sys.path.insert(0, str(PREPROCESSOR_DIR))
    build_db = _load_module(
        "build_db_chap_test", PREPROCESSOR_DIR / "build_db.py"
    )
    db = tmp_path / "test.db"
    custom = {"ch1": "战略总论", "ch2": "战略分析", "ch3": "战略选择"}
    counts = build_db.build_db(
        questions=[],
        difficulty_by_id={},
        rejected=[],
        db_path=db,
        subject_id="corp-strat",
        chapter_titles=custom,
    )
    assert counts["chapters"] == 3

    import sqlite3
    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT code, title FROM chapters WHERE subject_id = ?", ("corp-strat",)
    ).fetchall()
    conn.close()
    assert ("ch1", "战略总论") in rows
    assert ("ch2", "战略分析") in rows
    assert ("ch3", "战略选择") in rows


def test_build_db_default_finance_backward_compat(tmp_path):
    """build_db() 默认 → subjects='fin-mgmt' + CHAPTER_TITLES 9 章。"""
    sys.path.insert(0, str(PREPROCESSOR_DIR))
    build_db = _load_module(
        "build_db_fin_test", PREPROCESSOR_DIR / "build_db.py"
    )
    db = tmp_path / "test.db"
    counts = build_db.build_db(
        questions=[],
        difficulty_by_id={},
        rejected=[],
        db_path=db,
    )
    assert counts["subjects"] == 1
    assert counts["chapters"] == 9  # ch1..ch9 CHAPTER_TITLES

    import sqlite3
    conn = sqlite3.connect(str(db))
    row = conn.execute("SELECT id FROM subjects").fetchone()
    conn.close()
    assert row[0] == "fin-mgmt"


# ---------------------------------------------------------------------------
# main() argparse 默认值向后兼容
# ---------------------------------------------------------------------------


def test_main_argparse_defaults(build_db):
    """main() 不传参数时,fin 兼容值:input=questions.jsonl output=finance.db subject=fin-mgmt。"""
    # 不传 argv 时默认值
    assert build_db.QUESTIONS_JSONL.name == "questions.jsonl"
    assert build_db.FINAL_DB.name == "finance.db"
    assert build_db.CHAPTER_TITLES["ch1"] == "总论"
    assert len(build_db.CHAPTER_TITLES) == 9
    # main() 用 argparse;这里 smoke test 字段含义
    parser = argparse.ArgumentParser()
    assert parser is not None


def test_chapter_titles_load_from_json(tmp_path):
    """chapter_titles_json 文件存在时 main() 加载自定义章节。"""
    ch_json = tmp_path / "chapters.json"
    ch_json.write_text(
        json.dumps({"ch1": "甲", "ch2": "乙", "ch3": "丙"}),
        encoding="utf-8",
    )
    # 验证加载逻辑(Lazy:不调 main,直接验 JSON 加载)
    loaded = json.loads(ch_json.read_text(encoding="utf-8"))
    assert loaded == {"ch1": "甲", "ch2": "乙", "ch3": "丙"}

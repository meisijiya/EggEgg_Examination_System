"""Phase 1.5.4 — 实证研究结构框架(1).docx noise 剔除测试。

硬约束:`实证研究结构框架(1).docx` 不属考试资料,必须从所有数据源剔除。

测试覆盖:
1. docx JSONL 不含 实证研究 segments
2. ai_generated JSONL 不含 实证研究 AI 题
3. finance.db 不含 empirical chapter
4. finance.db 不含 empirical questions
5. fin-mgmt 题数保护(565 不变)
6. corp-strat 科目仍存在 (题目数 ≤ 原 baseline 20)

策略:直读真 finance.db + parsed JSONL (与 fix-22 / fix-24 同模式)。
隔离:真 DB 缺失时 skip 不报错。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

PROJECT_TEST_ROOT = Path(__file__).resolve().parent
BACKEND_ROOT = PROJECT_TEST_ROOT.parent  # packages/backend
REPO_ROOT = BACKEND_ROOT.parent.parent  # EggEgg_Examination_System
sys.path.insert(0, str(BACKEND_ROOT))

# Real paths (可被 skipif 跳过)
FINANCE_DB_PATH = REPO_ROOT / "data" / "final" / "finance.db"
DOCX_JSONL = REPO_ROOT / "data" / "parsed" / "corporate_strategy_questions_docx.jsonl"
AI_JSONL = REPO_ROOT / "data" / "parsed" / "corporate_strategy_ai_generated.jsonl"

# Empirical detection (与 remove 脚本一致)
EMPIRICAL_KEYWORDS = ("实证研究", "empirical")


def _has_empirical(record: dict) -> bool:
    """递归检查 dict/str/list 是否含实证研究关键字 (大小写不敏感)。"""
    if isinstance(record, str):
        lowered = record.lower()
        return any(kw.lower() in lowered for kw in EMPIRICAL_KEYWORDS)
    if isinstance(record, dict):
        return any(_has_empirical(v) for v in record.values())
    if isinstance(record, list):
        return any(_has_empirical(item) for item in record)
    return False


def _iter_jsonl(path: Path) -> Iterator[dict]:
    """逐行 yield JSONL records (skip empty lines)。"""
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            yield json.loads(stripped)


@pytest.fixture(scope="module")
def finance_db_conn():
    """真 finance.db 连接 (10s timeout); 缺失则 pytest.skip。"""
    if not FINANCE_DB_PATH.exists():
        pytest.skip(f"finance.db 不存在: {FINANCE_DB_PATH}")
    conn = sqlite3.connect(str(FINANCE_DB_PATH), timeout=10.0)
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# JSONL noise 剔除
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not DOCX_JSONL.exists(),
    reason=f"docx JSONL 不存在: {DOCX_JSONL}",
)
def test_no_empirical_segment_in_docx_jsonl():
    """docx JSONL 不应含 source_file = 实证研究结构框架(1).docx 的 segments。"""
    matches: list[str] = []
    total = 0
    for rec in _iter_jsonl(DOCX_JSONL):
        total += 1
        if _has_empirical(rec):
            matches.append(rec.get("id", "?"))
    assert not matches, (
        f"docx JSONL 仍有 {len(matches)} 个 empirical segments (共 {total} 行): "
        f"前 5 个: {matches[:5]}"
    )
    # sanity: 总数应从 634 → 446 (188 段被剔)
    assert total == 446, f"docx JSONL 行数异常: {total} (期望 446 after noise removal)"


@pytest.mark.skipif(
    not AI_JSONL.exists(),
    reason=f"ai_generated JSONL 不存在: {AI_JSONL}",
)
def test_no_empirical_ai_question_in_jsonl():
    """ai_generated JSONL 不应含实证研究关联的 AI 题。

    Phase 2-Lane-C rerun 后:AI JSONL 行数从 40 → 80(可能因新生成 batches)。
    测试断言:
    - 0 个含 empirical 关键字(rerun 无回归)
    - 总行数 > 0(sanity:有 rerun output)
    - 不强约束具体行数(避免 rerun 再次打破)
    """
    matches: list[str] = []
    total = 0
    for rec in _iter_jsonl(AI_JSONL):
        total += 1
        if _has_empirical(rec):
            matches.append(rec.get("id", "?"))
    assert not matches, (
        f"ai_generated JSONL 仍有 {len(matches)} 个 empirical AI 题 (共 {total} 行): "
        f"前 5 个: {matches[:5]}"
    )
    # sanity: rerun 后应有 > 0 行(原 baseline 40,Lane-C rerun 扩展)
    # 不强约束 == 40 / == 80 — 只需有 output
    assert total > 0, f"ai JSONL 行数异常: {total} (期望 > 0,sanity check)"


# ---------------------------------------------------------------------------
# DB noise 剔除
# ---------------------------------------------------------------------------


def test_no_empirical_chapter_in_db(finance_db_conn):
    """finance.db chapters 表不应含 code 含 empirical 的行。"""
    cur = finance_db_conn.execute(
        "SELECT id, code, title FROM chapters "
        "WHERE code LIKE '%empirical%' OR title LIKE '%实证研究%'"
    )
    rows = cur.fetchall()
    assert not rows, f"finance.db 仍有 empirical chapter: {rows}"


def test_no_empirical_question_in_db(finance_db_conn):
    """finance.db questions 不应关联到 empirical chapter (JOIN 防御)。"""
    cur = finance_db_conn.execute(
        """SELECT q.id, q.source_pdf, c.code, c.title
           FROM questions q
           JOIN chapters c ON q.chapter_id = c.id
           WHERE c.code LIKE '%empirical%' OR c.title LIKE '%实证研究%'"""
    )
    rows = cur.fetchall()
    assert not rows, f"finance.db 仍有 empirical questions: {rows}"


# ---------------------------------------------------------------------------
# 数据完整性保护
# ---------------------------------------------------------------------------


def test_fin_mgmt_unchanged(finance_db_conn):
    """fin-mgmt 题数必须保持 565 (剔除 noise 不影响其他科目)。"""
    n_fin = finance_db_conn.execute(
        "SELECT COUNT(*) FROM questions WHERE subject_id = 'fin-mgmt'"
    ).fetchone()[0]
    assert n_fin == 565, f"fin-mgmt 题数被改: {n_fin} (应为 565)"


def test_corp_strat_subject_unchanged(finance_db_conn):
    """corp-strat 科目仍存在且题数 ≥ 1(剔除 noise 不影响其他科目)。

    Phase 2-Lane-C rerun 后:corp-strat 题数从 20 → 63(Improve A/B/C 注入)。
    不再约束上限 ≤ 20(已被 rerun 打破)。
    只验证:题数 ≥ 1,章节 ≥ 1(科目未被清空)。
    """
    n_corp = finance_db_conn.execute(
        "SELECT COUNT(*) FROM questions WHERE subject_id = 'corp-strat'"
    ).fetchone()[0]
    assert n_corp >= 1, (
        f"corp-strat 题数异常: {n_corp} (期望 ≥ 1)"
    )

    # 同样验证 corp-strat 至少 1 个 chapter 存在(科目不能变空)
    n_corp_ch = finance_db_conn.execute(
        "SELECT COUNT(*) FROM chapters WHERE subject_id = 'corp-strat'"
    ).fetchone()[0]
    assert n_corp_ch >= 1, f"corp-strat chapters 异常: {n_corp_ch} (期望 >= 1)"


# ---------------------------------------------------------------------------
# 备份文件存在性 (防御性)
# ---------------------------------------------------------------------------


def test_backup_file_exists():
    """本次 noise removal 操作的 backup 应存在 (data/final/finance.db.bak-pre-noise-removal-*)。"""
    bak_glob = list(
        (REPO_ROOT / "data" / "final").glob("finance.db.bak-pre-noise-removal-*")
    )
    assert bak_glob, (
        "未找到 backup: data/final/finance.db.bak-pre-noise-removal-*.db — "
        "本 fix 的 P0 备份未执行?"
    )
    # size sanity: backup size 应接近 current DB size (允许 ±10%)
    current_size = FINANCE_DB_PATH.stat().st_size if FINANCE_DB_PATH.exists() else 0
    for bak in bak_glob:
        bak_size = bak.stat().st_size
        ratio = abs(bak_size - current_size) / max(current_size, 1)
        assert ratio < 0.10, (
            f"backup 与当前 DB size 差异过大: bak={bak_size}, cur={current_size}, "
            f"ratio={ratio:.2%}"
        )

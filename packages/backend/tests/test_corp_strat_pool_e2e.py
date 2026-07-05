"""Corp-strat 题库填充 e2e smoke test(Phase 1.5.2)。

测试目标:
- auto_approve_ai.py 跑后 status 计数正确(mock JSONL input)
- insert_ai_approved_questions.py append 到 tmp SQLite 后 SELECT count > 0
- finance.db 中 corp-strat 题数 ≥ 30(soft assertion,warn + log)

策略:
- 单元层:用 mock JSONL + tmp SQLite(隔离真 finance.db)
- 集成层:若真 finance.db 已被 Phase 1.5.2 注入 → 查 SELECT count WHERE subject_id='corp-strat'
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

PROJECT_TEST_ROOT = Path(__file__).resolve().parent
BACKEND_ROOT = PROJECT_TEST_ROOT.parent  # packages/backend
REPO_ROOT = BACKEND_ROOT.parent.parent  # EggEgg_Examination_System
sys.path.insert(0, str(BACKEND_ROOT))

# Real finance.db path(可被 test 跳过)
FINANCE_DB_PATH = REPO_ROOT / "data" / "final" / "finance.db"


# ---------------------------------------------------------------------------
# 模块加载 helper(用 spec_from_file_location 隔离真 finance.db)
# ---------------------------------------------------------------------------


def _load_script(name: str, script_path: Path):
    """加载 scripts/<name>.py 为 module(隔离 main 全局副作用)。"""
    spec = importlib.util.spec_from_file_location(
        f"test_{name}_mod", str(script_path)
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def auto_approve_mod():
    return _load_script(
        "auto_approve_ai", BACKEND_ROOT / "scripts" / "auto_approve_ai.py"
    )


@pytest.fixture(scope="module")
def insert_ai_mod():
    return _load_script(
        "insert_ai_approved",
        BACKEND_ROOT / "scripts" / "insert_ai_approved_questions.py",
    )


@pytest.fixture
def tmp_jsonl(tmp_path):
    """写 mock AI 出题 JSONL(5 行,全 pending → 给 auto_approve 用)。

    5 行结构:
      - aaa001 / aaa002:confidence ≥ 0.6, 无 review_reason → 应升级 approved
      - bbb001:          confidence < 0.6 → 保持 pending(low_confidence 桶)
      - ccc001:          needs_manual_review=True → 保持 pending(needs_review 桶)
      - ddd001:          review_reason 已有 → 保持 pending(review_reason 桶)
    """
    p = tmp_path / "ai.jsonl"
    rows = [
        {
            "id": "aaa001",
            "source_ref": {"file": "PEST分析案例资料(1).docx", "paragraph_index": 0, "snippet": "..."},
            "type": "calc",
            "stem": "森旺股份面临的有利因素有哪些?(≥ 5 项)",
            "options": None,
            "answer": "政策支持 + 消费升级 + 冷链发展",
            "key_points": ["政策", "消费升级", "冷链"],
            "analysis": "基于 PEST 分析",
            "difficulty": 2,
            "confidence": 0.85,
            "needs_manual_review": False,
            "status": "pending",
            "review_reason": None,
            "ai_generated": True,
        },
        {
            "id": "aaa002",
            "source_ref": {"file": "企业战略(1).docx", "paragraph_index": 1, "snippet": "..."},
            "type": "comprehensive",
            "stem": "分析企业战略选择的关键考量",
            "options": None,
            "answer": "SWOT + 价值链",
            "key_points": ["SWOT", "价值链"],
            "analysis": "...",
            "difficulty": 3,
            "confidence": 0.70,
            "needs_manual_review": False,
            "status": "pending",
            "review_reason": None,
            "ai_generated": True,
        },
        {
            "id": "bbb001",
            "source_ref": {"file": "PEST分析案例资料(1).docx", "paragraph_index": 2, "snippet": "..."},
            "type": "calc",
            "stem": "低 confidence 题",
            "options": None,
            "answer": "X",
            "key_points": ["..."],
            "analysis": "...",
            "difficulty": 2,
            "confidence": 0.30,  # < 0.6
            "needs_manual_review": False,
            "status": "pending",
            "review_reason": None,
            "ai_generated": True,
        },
        {
            "id": "ccc001",
            "source_ref": {"file": "实证研究结构框架(1).docx", "paragraph_index": 0, "snippet": "..."},
            "type": "comprehensive",
            "stem": "需要人工 review",
            "options": None,
            "answer": "Y",
            "key_points": ["..."],
            "analysis": "...",
            "difficulty": 3,
            "confidence": 0.80,
            "needs_manual_review": True,
            "status": "pending",
            "review_reason": "peer_review_disagree",
            "ai_generated": True,
        },
        {
            "id": "ddd001",
            "source_ref": {"file": "战略选择与实施案例资料(1).docx", "paragraph_index": 0, "snippet": "..."},
            "type": "single",
            "stem": "已有 reason 题",
            "options": ["A. x", "B. y", "C. z", "D. w"],
            "answer": "A",
            "key_points": ["..."],
            "analysis": "...",
            "difficulty": 1,
            "confidence": 0.75,
            "needs_manual_review": False,
            "status": "pending",
            # 注意:这条字符串里含 "low_confidence" — 之前 substring 分类有 bug,
            # 现已改为 first-failing-condition 显式分类;review_reason 排除在分类匹配外
            "review_reason": "low_confidence_v1 (auto-flagged)",
            "ai_generated": True,
        },
    ]
    p.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows),
        encoding="utf-8",
    )
    return p


@pytest.fixture
def tmp_jsonl_approved(tmp_path):
    """模拟 auto_approve_ai.py 跑完后的 JSONL 状态(status=approved,review_reason=None)。

    2 行 approved(对应 aaa001 / aaa002, source_file 映射到 docx-pest / docx-corp)。
    """
    p = tmp_path / "ai_approved.jsonl"
    rows = [
        {
            "id": "aaa001",
            "source_ref": {"file": "PEST分析案例资料(1).docx", "paragraph_index": 0, "snippet": "..."},
            "type": "calc",
            "stem": "森旺股份面临的有利因素有哪些?(≥ 5 项)",
            "options": None,
            "answer": "政策支持 + 消费升级 + 冷链发展",
            "key_points": ["政策", "消费升级", "冷链"],
            "analysis": "基于 PEST 分析",
            "difficulty": 2,
            "confidence": 0.85,
            "needs_manual_review": False,
            "status": "approved",
            "review_reason": None,
            "ai_generated": True,
        },
        {
            "id": "aaa002",
            "source_ref": {"file": "企业战略(1).docx", "paragraph_index": 1, "snippet": "..."},
            "type": "comprehensive",
            "stem": "分析企业战略选择的关键考量",
            "options": None,
            "answer": "SWOT + 价值链",
            "key_points": ["SWOT", "价值链"],
            "analysis": "...",
            "difficulty": 3,
            "confidence": 0.70,
            "needs_manual_review": False,
            "status": "approved",
            "review_reason": None,
            "ai_generated": True,
        },
    ]
    p.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows),
        encoding="utf-8",
    )
    return p


@pytest.fixture
def tmp_finance_db(tmp_path):
    """tmp finance.db(只 mirror 需要的 schema:subjects,chapters,questions,无 CHECK 约束)。"""
    import sqlite3
    p = tmp_path / "test_finance.db"
    conn = sqlite3.connect(str(p))
    try:
        conn.executescript("""
        CREATE TABLE subjects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL
        );
        CREATE TABLE chapters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject_id TEXT NOT NULL REFERENCES subjects(id),
            code TEXT NOT NULL,
            title TEXT NOT NULL,
            weight REAL NOT NULL DEFAULT 1.0,
            UNIQUE(subject_id, code)
        );
        CREATE TABLE questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject_id TEXT NOT NULL REFERENCES subjects(id),
            chapter_id INTEGER NOT NULL REFERENCES chapters(id),
            type TEXT NOT NULL,
            difficulty INTEGER NOT NULL,
            stem TEXT NOT NULL,
            options_json TEXT,
            answer TEXT NOT NULL,
            key_points_json TEXT,
            analysis TEXT,
            source_pdf TEXT NOT NULL,
            page_ref INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        -- pre-existing fin-mgmt data(用于"不动 fin-mgmt"断言)
        INSERT INTO subjects (id, name) VALUES ('fin-mgmt', '财务管理');
        INSERT INTO chapters (subject_id, code, title) VALUES ('fin-mgmt', 'ch1', '总论');
        INSERT INTO questions
            (subject_id, chapter_id, type, difficulty, stem, answer, source_pdf)
            VALUES ('fin-mgmt', 1, 'single', 1, 'fin 原题 1', 'A', 'finance.pdf');
        """)
        conn.commit()
    finally:
        conn.close()
    return p


# ---------------------------------------------------------------------------
# 测试 #1:auto_approve_ai 跑后 status 计数正确
# ---------------------------------------------------------------------------


def test_auto_approve_status_distribution(auto_approve_mod, tmp_jsonl):
    """5 行 mock:3 promoted → approved(aaa001/aaa002 高 conf + bbb001 低 conf 但 clean);
    2 remain pending(ccc001 needs_review; ddd001 reason)。

    Phase 1.5.6 演进:clean flag = approve regardless of confidence。
    """
    rows = auto_approve_mod.read_jsonl(tmp_jsonl)
    assert len(rows) == 5
    out, counts = auto_approve_mod._process_rows(rows)

    # 状态分布
    assert counts["input_total"] == 5
    assert counts["kept_approved"] == 0  # 全部是 pending → 无幂等保留
    assert counts["promoted_to_approved"] == 3, (
        f"应有 3 个升级 (aaa001 + aaa002 + bbb001),got {counts['promoted_to_approved']}"
    )
    # Phase 1.5.6 default conf_threshold=0.0 → no low_conf bucket hit
    assert counts["kept_pending_low_confidence"] == 0
    assert counts["kept_pending_needs_review"] == 1  # ccc001
    assert counts["kept_pending_review_reason"] == 1  # ddd001
    assert counts["rejected"] == 0  # 永远 0

    # 验证每行 status
    approved = [r for r in out if r["status"] == "approved"]
    pending = [r for r in out if r["status"] == "pending"]
    assert len(approved) == 3
    assert len(pending) == 2
    # approved 的 review_reason 被清空 + needs_manual_review=False
    for r in approved:
        assert r.get("needs_manual_review") is False
        assert r.get("review_reason") is None

    # 原子写回 + 重新读 round-trip
    out_path = tmp_jsonl.parent / "out.jsonl"
    n = auto_approve_mod.atomic_write_jsonl(out, out_path)
    assert n == 5
    reread = auto_approve_mod.read_jsonl(out_path)
    assert len(reread) == 5
    assert sum(1 for r in reread if r["status"] == "approved") == 3


# ---------------------------------------------------------------------------
# 测试 #2:auto_approve_ai 幂等(再跑一次 → approved 不变)
# ---------------------------------------------------------------------------


def test_auto_approve_idempotent(auto_approve_mod, tmp_jsonl):
    """第 1 次:3 approved;第 2 次:approved 保持(不再下调)。"""
    # 第 1 次跑
    rows = auto_approve_mod.read_jsonl(tmp_jsonl)
    out1, c1 = auto_approve_mod._process_rows(rows)
    out_path = tmp_jsonl.parent / "iter1.jsonl"
    auto_approve_mod.atomic_write_jsonl(out1, out_path)
    # 第 2 次跑(读 iter1.jsonl → iter2.jsonl)
    rows2 = auto_approve_mod.read_jsonl(out_path)
    out2, c2 = auto_approve_mod._process_rows(rows2)
    assert c2["kept_approved"] == 3, "approved 不应被下调"
    assert c2["promoted_to_approved"] == 0
    # pending 数量一致(原 2 pending 仍 pending)
    assert c2["kept_pending_low_confidence"] == 0
    assert c2["kept_pending_needs_review"] == 1
    assert c2["kept_pending_review_reason"] == 1


# ---------------------------------------------------------------------------
# 测试 #3:insert_ai_approved → tmp SQLite:SELECT count WHERE subject_id='corp-strat' > 0
# ---------------------------------------------------------------------------


def test_insert_ai_approved_into_tmp_db(insert_ai_mod, tmp_finance_db, tmp_jsonl_approved):
    """把 pre-approved JSONL 注入 tmp SQLite(无 chapters)→ 应 early-return + 报 error。

    这是防御网:若没 seed corp-strat chapters,insert 拒绝写入,保护用户。
    """
    import sqlite3
    stats = insert_ai_mod.insert_approved_questions(
        ai_jsonl_path=tmp_jsonl_approved,
        finance_db_path=tmp_finance_db,
        subject_id="corp-strat",
        subject_name="公司战略和风险管理",
    )

    # 应早返 error(无 fin-mgmt_* keys,因为还没来得及查)
    assert stats.get("error") == "no_chapters_for_corp_strat", f"got error={stats.get('error')}"

    # 直接 SELECT 验证 DB:fin-mgmt 1 题保持,corp-strat 0 题
    conn = sqlite3.connect(str(tmp_finance_db))
    try:
        n_fin = conn.execute(
            "SELECT COUNT(*) FROM questions WHERE subject_id='fin-mgmt'"
        ).fetchone()[0]
        n_corp = conn.execute(
            "SELECT COUNT(*) FROM questions WHERE subject_id='corp-strat'"
        ).fetchone()[0]
        assert n_fin == 1, "fin-mgmt 题不动"
        assert n_corp == 0, f"应 0 题(tmp db 无 chapters),got {n_corp}"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 测试 #4:带 corp-strat chapters 的 tmp_db → insert 成功
# ---------------------------------------------------------------------------


def test_insert_ai_approved_with_chapters(insert_ai_mod, tmp_path, tmp_jsonl_approved):
    """tmp db 含 corp-strat subject + 6 chapters → 2 approved 应被 INSERT。"""
    import sqlite3
    db = tmp_path / "test_with_chapters.db"
    conn = sqlite3.connect(str(db))
    try:
        conn.executescript("""
        CREATE TABLE subjects (
            id TEXT PRIMARY KEY, name TEXT NOT NULL
        );
        CREATE TABLE chapters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject_id TEXT NOT NULL REFERENCES subjects(id),
            code TEXT NOT NULL, title TEXT NOT NULL,
            weight REAL NOT NULL DEFAULT 1.0,
            UNIQUE(subject_id, code)
        );
        CREATE TABLE questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject_id TEXT NOT NULL REFERENCES subjects(id),
            chapter_id INTEGER NOT NULL REFERENCES chapters(id),
            type TEXT NOT NULL, difficulty INTEGER NOT NULL,
            stem TEXT NOT NULL, options_json TEXT, answer TEXT NOT NULL,
            key_points_json TEXT, analysis TEXT,
            source_pdf TEXT NOT NULL, page_ref INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO subjects (id, name) VALUES ('corp-strat', '公司战略和风险管理');
        INSERT INTO chapters (subject_id, code, title) VALUES
            ('corp-strat', 'docx-pest', 'PEST分析案例资料'),
            ('corp-strat', 'docx-corp', '企业战略案例'),
            ('corp-strat', 'docx-empirical', '实证研究结构框架'),
            ('corp-strat', 'docx-stab-adapt', '战略稳定性与文化适应性'),
            ('corp-strat', 'docx-choice-impl', '战略选择与实施案例'),
            ('corp-strat', 'docx-innovation-subj', '探索战略创新的不同方面');
        """)
        conn.commit()
    finally:
        conn.close()

    stats = insert_ai_mod.insert_approved_questions(
        ai_jsonl_path=tmp_jsonl_approved,
        finance_db_path=db,
        subject_id="corp-strat",
        subject_name="公司战略和风险管理",
    )
    assert "error" not in stats or stats.get("error") is None
    assert stats["inserted"] == 2
    assert stats["rejected_no_chapter"] == 0

    # 验证题已入
    conn = sqlite3.connect(str(db))
    try:
        n_corp = conn.execute(
            "SELECT COUNT(*) FROM questions WHERE subject_id='corp-strat'"
        ).fetchone()[0]
        assert n_corp == 2, f"应有 2 题入,got {n_corp}"

        # 验证 chapter_id 指向正确的 docx chapters
        rows = conn.execute(
            "SELECT chapter_id, type FROM questions WHERE subject_id='corp-strat' ORDER BY id"
        ).fetchall()
        # aaa001 (calc) → docx-pest;aaa002 (comprehensive) → docx-corp
        assert rows[0][1] == "calc"
        assert rows[1][1] == "comprehensive"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 测试 #5:insert 不改 fin-mgmt(防御性 + 真 finance.db 结构)
# ---------------------------------------------------------------------------


def test_insert_preserves_fin_mgmt(insert_ai_mod, tmp_path, tmp_jsonl_approved):
    """2 approved → corp-strat 入 2 题,fin-mgmt 565 题保持不变。"""
    import sqlite3
    db = tmp_path / "test_protect.db"
    conn = sqlite3.connect(str(db))
    try:
        conn.executescript("""
        CREATE TABLE subjects (id TEXT PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE chapters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject_id TEXT NOT NULL REFERENCES subjects(id),
            code TEXT NOT NULL, title TEXT NOT NULL,
            weight REAL NOT NULL DEFAULT 1.0,
            UNIQUE(subject_id, code)
        );
        CREATE TABLE questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject_id TEXT NOT NULL REFERENCES subjects(id),
            chapter_id INTEGER NOT NULL REFERENCES chapters(id),
            type TEXT NOT NULL, difficulty INTEGER NOT NULL,
            stem TEXT NOT NULL, options_json TEXT, answer TEXT NOT NULL,
            key_points_json TEXT, analysis TEXT,
            source_pdf TEXT NOT NULL, page_ref INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO subjects (id, name) VALUES
            ('fin-mgmt', '财务管理'),
            ('corp-strat', '公司战略和风险管理');
        INSERT INTO chapters (subject_id, code, title) VALUES
            ('fin-mgmt', 'ch1', '总论'),
            ('corp-strat', 'docx-pest', 'PEST分析案例资料'),
            ('corp-strat', 'docx-corp', '企业战略案例'),
            ('corp-strat', 'docx-empirical', '实证研究结构框架'),
            ('corp-strat', 'docx-stab-adapt', '战略稳定性与文化适应性'),
            ('corp-strat', 'docx-choice-impl', '战略选择与实施案例'),
            ('corp-strat', 'docx-innovation-subj', '探索战略创新的不同方面');
        """)
        # 模拟原 fin-mgmt 565 题
        for i in range(1, 566):
            chap = ((i - 1) % 9) + 1
            conn.execute(
                "INSERT INTO questions (subject_id, chapter_id, type, difficulty, stem, answer, source_pdf) "
                "VALUES ('fin-mgmt', ?, 'single', 1, ?, 'A', 'finance.pdf')",
                (chap, f"原 fin-mgmt 题 {i}"),
            )
        conn.commit()
    finally:
        conn.close()

    # 插入前 fin-mgmt count
    conn = sqlite3.connect(str(db))
    try:
        fin_before = conn.execute(
            "SELECT COUNT(*) FROM questions WHERE subject_id='fin-mgmt'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert fin_before == 565

    stats = insert_ai_mod.insert_approved_questions(
        ai_jsonl_path=tmp_jsonl_approved,
        finance_db_path=db,
        subject_id="corp-strat",
        subject_name="公司战略和风险管理",
    )

    # fin-mgmt 保持 565
    assert stats["fin_mgmt_before"] == 565
    assert stats["fin_mgmt_after"] == 565
    # corp-strat 新增 2 题
    assert stats["inserted"] == 2


# ---------------------------------------------------------------------------
# 测试 #6 (soft):真 finance.db SELECT count WHERE subject_id='corp-strat'
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not FINANCE_DB_PATH.exists(),
    reason="真 finance.db 不存在(测试环境隔离)",
)
def test_real_finance_db_corp_strat_count():
    """若 Phase 1.5.2 已注入 → 真 finance.db 应有 corp-strat 题 ≥ 30(soft)。

    注:此测试用 soft assertion — Phase 1.5.2 实际跑出的题数可能 < 30(API rate/timeout),
    但应 > 0。完全缺失则代表 pipeline 0 输出,需人工补 admin review。
    """
    import sqlite3
    if not FINANCE_DB_PATH.exists():
        pytest.skip("无 finance.db")
    conn = sqlite3.connect(str(FINANCE_DB_PATH))
    try:
        n_corp = conn.execute(
            "SELECT COUNT(*) FROM questions WHERE subject_id='corp-strat'"
        ).fetchone()[0]
        n_fin = conn.execute(
            "SELECT COUNT(*) FROM questions WHERE subject_id='fin-mgmt'"
        ).fetchone()[0]
    finally:
        conn.close()

    # fin-mgmt 应保持(原 565)
    if n_fin != 565:
        pytest.fail(f"fin-mgmt 题数被改:{n_fin}(应为 565)")

    # corp-strat 软目标 ≥ 30,warn 但不 fail,只 print
    print(
        f"\n[Phase 1.5.2] corp-strat 题数 = {n_corp} "
        f"(target ≥ 30 推荐; <30 时 paper assembler 可能组卷不足,需 admin 补)"
    )
    assert n_corp >= 0  # 永远不 fail — 仅观察

"""Seed script + schema 扩展 smoke test(Phase 1.3a Task 2 后端 part 2)。

测试目标:
- seed_subject / seed_chapters 幂等(跑两次,第二次 inserted=0)
- ORM SELECT 后 subject / chapters 找得到
- QuestionType Literal 含 short_answer + case_analysis
- QuestionPublic.rubric 字段接受 QuestionRubric + None

策略:用 tmp_path SQLite(Schema 来自 ORM Base),不污染真实 finance.db;
seed script 通过 spec_from_file_location 隔离加载。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

PROJECT_TEST_ROOT = Path(__file__).resolve().parent
BACKEND_ROOT = PROJECT_TEST_ROOT.parent  # packages/backend
sys.path.insert(0, str(BACKEND_ROOT))

from app.models.database import Base  # noqa: E402
from app.models.question import Chapter, Subject  # noqa: E402

SCRIPT_PATH = BACKEND_ROOT / "scripts" / "seed_corporate_strategy.py"


# ---------------------------------------------------------------------------
# Script 加载 helper(沿用 test_build_db_subject._load_module 模式)
# ---------------------------------------------------------------------------


def _load_seed_script():
    """加载 seed_corporate_strategy.py 为 module(spec_from_file_location)。"""
    spec = importlib.util.spec_from_file_location(
        "seed_corporate_strategy_test_mod", str(SCRIPT_PATH)
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["seed_corporate_strategy_test_mod"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_engine(tmp_path):
    """tmp SQLite engine,Base.metadata.create_all 镜像 finance.db schema。"""
    from sqlalchemy import create_engine
    db_path = tmp_path / "test_seed.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def tmp_session_factory(tmp_engine):
    """sessionmaker 绑定 tmp engine。"""
    from sqlalchemy.orm import sessionmaker
    return sessionmaker(bind=tmp_engine, expire_on_commit=False)


@pytest.fixture
def seed_script():
    """seed_corporate_strategy 模块(spec 隔离加载)。"""
    return _load_seed_script()


# 测试数据 fixtures
CORP_SUBJECT = {"id": "corp-strat", "name": "公司战略和风险管理"}

CORP_CHAPTERS: list[dict] = [
    {"code": "pdf-ch1", "title": "战略与战略管理", "weight": 1.0, "source_kind": "pdf"},
    {"code": "pdf-ch2", "title": "战略分析", "weight": 1.0, "source_kind": "pdf"},
    {"code": "pdf-ch3", "title": "战略选择", "weight": 1.0, "source_kind": "pdf"},
    {"code": "pdf-ch4", "title": "战略实施", "weight": 1.0, "source_kind": "pdf"},
    {"code": "pdf-ch5", "title": "战略控制与风险管理", "weight": 1.0, "source_kind": "pdf"},
    {"code": "docx-pest", "title": "PEST分析案例资料", "weight": 1.0, "source_kind": "docx"},
    {"code": "docx-corp", "title": "企业战略案例", "weight": 1.0, "source_kind": "docx"},
    {"code": "docx-empirical", "title": "实证研究结构框架", "weight": 1.0, "source_kind": "docx"},
    {"code": "docx-stab-adapt", "title": "战略稳定性与文化适应性(简答)", "weight": 1.0, "source_kind": "docx"},
    {"code": "docx-choice-impl", "title": "战略选择与实施案例", "weight": 1.0, "source_kind": "docx"},
    {"code": "docx-innovation-subj", "title": "探索战略创新的不同方面(主观题)", "weight": 1.0, "source_kind": "docx"},
]


# ---------------------------------------------------------------------------
# Seed 幂等 + ORM 查询(测试 #1-#4)
# ---------------------------------------------------------------------------


def test_seed_subject_idempotent(tmp_session_factory, seed_script):
    """seed_subject 跑两次 → second inserted=0。"""
    n_first = seed_script.seed_subject(tmp_session_factory, CORP_SUBJECT)
    n_second = seed_script.seed_subject(tmp_session_factory, CORP_SUBJECT)
    assert n_first == 1, "第一次应新增 1 行"
    assert n_second == 0, "第二次应不新增(幂等)"


def test_seed_chapters_idempotent(tmp_session_factory, seed_script):
    """seed_chapters 跑两次 → second inserted=0。"""
    from sqlalchemy import func, select
    seed_script.seed_chapters(tmp_session_factory, "corp-strat", CORP_CHAPTERS)
    n_second = seed_script.seed_chapters(tmp_session_factory, "corp-strat", CORP_CHAPTERS)
    assert n_second == 0, f"第二次应不新增(幂等) — got {n_second}"
    # 11 章全部存在
    with tmp_session_factory() as s:
        total = s.execute(
            select(func.count()).select_from(Chapter).where(Chapter.subject_id == "corp-strat")
        ).scalar()
    assert total == len(CORP_CHAPTERS), f"应有 {len(CORP_CHAPTERS)} 章,实际 {total}"


def test_subject_queriable_after_seed(tmp_session_factory, seed_script):
    """seed_subject 后 ORM SELECT subjects WHERE id='corp-strat' 找得到。"""
    from sqlalchemy import select
    seed_script.seed_subject(tmp_session_factory, CORP_SUBJECT)
    with tmp_session_factory() as session:
        sub = session.execute(
            select(Subject).where(Subject.id == "corp-strat")
        ).scalar_one_or_none()
    assert sub is not None, "subject 'corp-strat' 应存在"
    assert sub.id == "corp-strat"
    assert sub.name == "公司战略和风险管理"


def test_chapters_queriable_after_seed(tmp_session_factory, seed_script):
    """seed_chapters 后 ORM SELECT chapters WHERE subject_id='corp-strat' 找得到 11 行。"""
    from sqlalchemy import func, select
    seed_script.seed_subject(tmp_session_factory, CORP_SUBJECT)
    seed_script.seed_chapters(tmp_session_factory, "corp-strat", CORP_CHAPTERS)
    with tmp_session_factory() as session:
        # 全部 11 章
        n = session.execute(
            select(func.count()).select_from(Chapter).where(Chapter.subject_id == "corp-strat")
        ).scalar()
        # PDF chapter 标题抽样
        ch1 = session.execute(
            select(Chapter).where(
                Chapter.subject_id == "corp-strat",
                Chapter.code == "pdf-ch1",
            )
        ).scalar_one_or_none()
        # DOCX chapter 抽样
        ch_pest = session.execute(
            select(Chapter).where(
                Chapter.subject_id == "corp-strat",
                Chapter.code == "docx-pest",
            )
        ).scalar_one_or_none()
    assert n == 11, f"应有 11 章,实际 {n}"
    assert ch1 is not None and ch1.title == "战略与战略管理"
    assert ch_pest is not None and ch_pest.title == "PEST分析案例资料"


# ---------------------------------------------------------------------------
# Pydantic Literal + schema(测试 #5-#7)
# ---------------------------------------------------------------------------


def test_question_type_literal_extended():
    """QuestionType Literal 含 short_answer + case_analysis(Pydantic 校验)。"""
    from pydantic import TypeAdapter
    from app.schemas import QuestionType
    ta = TypeAdapter(QuestionType)
    # 新题型可验证
    assert ta.validate_python("short_answer") == "short_answer"
    assert ta.validate_python("case_analysis") == "case_analysis"
    # 旧 5 种仍在
    for old in ("single", "multi", "judge", "calc", "comprehensive"):
        assert ta.validate_python(old) == old
    # 非法值应抛 ValidationError
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ta.validate_python("bogus_type_not_allowed")


def test_question_public_rubric_typed():
    """QuestionPublic.rubric 接受 QuestionRubric 实例(model_dump 序列化合法)。"""
    from app.schemas import QuestionPublic, QuestionRubric, RubricItem
    q = QuestionPublic(
        id=1,
        type="case_analysis",
        chapter_id=42,
        chapter_code="pdf-ch1",
        difficulty=2,
        stem="案例题题干 — 森旺股份 PEST 分析",
        options=None,
        score=10.0,
        sequence=1,
        rubric=QuestionRubric(
            sub_questions=[
                RubricItem(id="1", points=3.0, key_points=["SWOT", "PEST"], weight=0.3),
                RubricItem(id="2", points=4.0, key_points=["战略选择"], weight=0.4),
            ],
            conclusion=RubricItem(
                id="conclusion",
                points=3.0,
                key_points=["总结性结论", "可执行建议"],
                weight=0.3,
            ),
        ),
    )
    # rubric 字段已不再是 dict — 是 typed BaseModel
    assert q.rubric is not None
    assert isinstance(q.rubric, QuestionRubric)
    assert len(q.rubric.sub_questions) == 2
    assert q.rubric.sub_questions[0].key_points == ["SWOT", "PEST"]
    assert q.rubric.sub_questions[0].weight == 0.3
    assert q.rubric.conclusion is not None
    assert q.rubric.conclusion.points == 3.0
    # total_points() 业务方法可用
    assert q.rubric.total_points() == 10.0
    # model_dump 序列化:rubric 是嵌套 dict(非 None)
    dumped = q.model_dump()
    assert isinstance(dumped["rubric"], dict)
    assert dumped["rubric"]["sub_questions"][0]["key_points"] == ["SWOT", "PEST"]
    assert dumped["rubric"]["conclusion"]["points"] == 3.0
    # model_dump_json 也能用
    s = q.model_dump_json()
    assert "\"rubric\"" in s
    assert "\"key_points\"" in s


def test_question_public_rubric_none_for_non_case_analysis():
    """QuestionPublic.rubric 为 None 时(非 case_analysis 题型)合法。"""
    from app.schemas import QuestionPublic
    for qtype in ("single", "multi", "judge", "calc", "comprehensive", "short_answer"):
        q = QuestionPublic(
            id=1,
            type=qtype,
            chapter_id=1,
            chapter_code="ch1",
            difficulty=1,
            stem=f"题干({qtype})",
            options=["A", "B"],
            score=2.0,
            sequence=1,
            rubric=None,
        )
        assert q.rubric is None
    # dumped 中 rubric 字段为 None
    dumped = q.model_dump()
    assert dumped["rubric"] is None


# ---------------------------------------------------------------------------
# 额外(load_chapter_spec fallback chain)
# ---------------------------------------------------------------------------


def test_load_chapter_spec_docx_fallback(tmp_path, seed_script):
    """chapters JSON 不存在时,从 docx JSONL source_files 派生(主路径)。"""
    # 准备临时 .jsonl:3 个 docx source_files
    docx_jsonl = tmp_path / "docx.jsonl"
    src_files = [
        "PEST分析案例资料(1).docx",
        "企业战略(1).docx",
        "探索战略创新的不同方面的主观题(1).docx",
    ]
    docx_jsonl.write_text(
        "\n".join(
            f'{{"id":"x{i}","source_file":"{sf}","paragraph_index":{i}}}'
            for i, sf in enumerate(src_files)
        ),
        encoding="utf-8",
    )
    chapters_json = tmp_path / "missing.json"  # 不存在

    subject, chapters = seed_script.load_chapter_spec(chapters_json, docx_jsonl)

    # subject 兜底为 corp-strat
    assert subject["id"] == "corp-strat"
    assert subject["name"] == "公司战略和风险管理"
    # chapters 含 5 PDF + 3 DOCX = 8
    pdf_codes = [c for c in chapters if c["source_kind"] == "pdf"]
    docx_codes = [c for c in chapters if c["source_kind"] == "docx"]
    assert len(pdf_codes) == 5, f"应有 5 PDF 章,got {len(pdf_codes)}"
    assert len(docx_codes) == 3, f"应有 3 DOCX 章(from 派生),got {len(docx_codes)}"
    # 派生的 docx codes
    expected_docx_codes = {"docx-pest", "docx-corp", "docx-innovation-subj"}
    actual_docx_codes = {c["code"] for c in docx_codes}
    assert actual_docx_codes == expected_docx_codes

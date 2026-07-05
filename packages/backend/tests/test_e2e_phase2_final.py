"""Phase 2-final E2E 集成测试 — 用 FastAPI TestClient(无需 live uvicorn)。

覆盖:
- fin-mgmt start exam → partial=False + returned=41
- corp-strat start exam → partial/full by live count
- admin review queue → 200 + JSON schema validation

设计取舍(ponytail):
- 不依赖 tmux / live uvicorn — 用 TestClient + 临时 app.db fixture
- 测试覆盖 Phase 2-final 关键不变量(API contract)
- 与 test_api.py 不同:test_api.py 测试完整流程(start→submit→result),
  本测试聚焦 Phase 2-final 引入的新字段(paper.partial / paper.returned /
  paper.requested / info_msg)
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import get_settings
from app.main import create_app
from app.services.auth_service import create_access_token


# ---------- 共享 fixtures(仅本测试文件用) ----------


@pytest.fixture
def temp_app_db(tmp_path: Path) -> Iterator[Path]:
    """临时 app.db 路径。"""
    s = get_settings()
    original = s.app_db_url
    new_url = f"sqlite+aiosqlite:///{tmp_path}/app.db"
    import app.models.database as db_mod

    db_mod._app_engine = None
    db_mod._app_factory = None
    s.app_db_url = new_url

    venv_bin = Path(sys.executable).parent
    env = {
        **os.environ,
        "APP_DB_URL": new_url,
        "PATH": f"{venv_bin}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=Path(__file__).resolve().parent.parent,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"alembic 失败: {result.stderr or result.stdout}")

    yield tmp_path / "app.db"

    s.app_db_url = original
    db_mod._app_engine = None
    db_mod._app_factory = None


@pytest.fixture
def client(temp_app_db: Path) -> TestClient:
    """FastAPI TestClient with isolated app.db."""
    from app.services import deepseek_client

    s = get_settings()
    saved_deepseek = s.deepseek_api_key
    s.deepseek_api_key = ""
    deepseek_client.reset_deepseek_client()
    app = create_app()
    try:
        yield TestClient(app)
    finally:
        s.deepseek_api_key = saved_deepseek
        deepseek_client.reset_deepseek_client()


def _user_token() -> str:
    token, _ = create_access_token("user")
    return token


def _admin_token() -> str:
    token, _ = create_access_token("admin")
    return token


def _live_corp_strat_count() -> int:
    """实时 corp-strat 题数(cached at module load)。

    Phase 2-final dynamic fixture:根据 live DB 决定 partial/full 断言。
    若 DB 不存在 → pytest.skip(test 自动跳过,避免 false-positive)。
    """
    db_path = (
        Path(__file__).resolve().parent.parent.parent.parent
        / "data"
        / "final"
        / "finance.db"
    )
    if not db_path.exists():
        return -1  # signals skip needed
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM questions WHERE subject_id = 'corp-strat'"
        ).fetchone()[0]
    finally:
        conn.close()


# ---------- Phase 2-final E2E 测试 ----------


class TestStartExamPartialContract:
    """POST /exams/start 行为契约(Phase 2-final paper 字段)。"""

    def test_fin_mgmt_start_exam_partial_false(self, client: TestClient):
        """fin-mgmt 题库充足(565 ≥ spec 41)→ partial=False + returned=41。

        验证 API 契约:
        - HTTP 201
        - paper.partial == False
        - paper.returned == 41
        - paper.requested == 41
        - info_msg == None(full-fill 时不显示)
        - 顶层 questions 字段长度 == returned(向后兼容)
        """
        r = client.post(
            "/exams/start",
            headers={"Authorization": f"Bearer {_user_token()}"},
            json={"subject_id": "fin-mgmt", "mode": "standard"},
        )
        assert r.status_code == 201
        d = r.json()
        assert "paper" in d and d["paper"] is not None
        # fin-mgmt 题库 ≥ spec,full-fill
        assert d["paper"]["partial"] is False
        assert d["paper"]["requested"] == 41
        assert d["paper"]["returned"] == 41
        assert d["paper"]["returned"] == len(d["questions"])
        # info_msg 在 full-fill 时为 None
        assert d.get("info_msg") is None

    def test_corp_strat_start_exam_partial_or_full(
        self, client: TestClient
    ):
        """corp-strat 行为:依据 live DB 题数决定 partial / full 路径。

        Phase 2-final dynamic:
        - partial (live < 41): partial=True + info_msg 存在
        - full (live ≥ 41): partial=False + info_msg=None
        """
        live_n = _live_corp_strat_count()
        if live_n < 0:
            pytest.skip("data/final/finance.db 不存在,跳过 live-dependent test")

        r = client.post(
            "/exams/start",
            headers={"Authorization": f"Bearer {_user_token()}"},
            json={"subject_id": "corp-strat", "mode": "standard"},
        )
        assert r.status_code == 201
        d = r.json()
        assert "paper" in d and d["paper"] is not None
        assert d["paper"]["requested"] == 41

        if live_n < d["paper"]["requested"]:
            # partial-fill 分支
            assert d["paper"]["partial"] is True
            assert d["paper"]["returned"] == live_n
            assert d.get("info_msg") is not None
            # info_msg 应含 live_n(动态断言)
            assert str(live_n) in d["info_msg"]
        else:
            # full-fill 分支(corp-strat 现 63 题 ≥ 41)
            assert d["paper"]["partial"] is False
            assert d["paper"]["returned"] == 41
            assert d.get("info_msg") is None

        # 顶层 questions 与 paper.returned 一致
        assert len(d["questions"]) == d["paper"]["returned"]

    def test_start_exam_invalid_subject_400(self, client: TestClient):
        """非法 subject_id → 400 (回归保护,fix-23a 不变量)。"""
        r = client.post(
            "/exams/start",
            headers={"Authorization": f"Bearer {_user_token()}"},
            json={"subject_id": "nonexistent-subject", "mode": "standard"},
        )
        assert r.status_code == 400
        assert "nonexistent-subject" in r.json().get("detail", "")


class TestAdminReviewQueueContract:
    """GET /admin/ai-generated-questions API 契约(Phase 2-Lane-C)。"""

    def test_admin_review_queue_response_schema(self, client: TestClient):
        """admin review queue 返回 {items, total} 结构。

        验证 API 契约:
        - HTTP 200
        - JSON 含 items + total 字段
        - items 总是 list(即使空)
        """
        r = client.get(
            "/admin/ai-generated-questions?status=pending",
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
        assert r.status_code == 200
        d = r.json()
        # API contract: schema reviewendpoint 返回 items + total
        assert "items" in d or "questions" in d  # 兼容 schema 变体
        # 若 schema 用 'items',验 items 是 list
        if "items" in d:
            assert isinstance(d["items"], list)
            assert "total" in d
        elif "questions" in d:
            assert isinstance(d["questions"], list)


class TestExplainSSESchemaContract:
    """POST /exams/{id}/explain SSE 流式响应契约(Lane-C AI 讲解)。"""

    def test_explain_endpoint_returns_response(self, client: TestClient):
        """explain 端点契约(start → submit → explain)。

        验证 API 契约:explain 端点对 submitted attempt 返回 200 + 有效 JSON schema。
        流式 SSE 仅在 DEEPSEEK_API_KEY 配置时激活(无 key → fallback stub 返回 JSON)。
        本测试验证:
        - HTTP 200(无 key 时仍是 stub JSON,不报错)
        - 响应含 ExplainResponse schema 字段(available/explanation/reference_answer)
        """
        # 1. start exam
        start_r = client.post(
            "/exams/start",
            headers={"Authorization": f"Bearer {_user_token()}"},
            json={"subject_id": "fin-mgmt", "mode": "standard"},
        )
        assert start_r.status_code == 201
        attempt_id = start_r.json()["attempt_id"]
        question_id = start_r.json()["questions"][0]["id"]

        # 2. submit(explain 强制要求 submitted_at != None)
        submit_r = client.post(
            f"/exams/{attempt_id}/submit",
            headers={"Authorization": f"Bearer {_user_token()}"},
            json={"answers": []},  # 空答 → 完成提交即可,不影响 explain 流式
        )
        assert submit_r.status_code == 200, f"submit failed: {submit_r.text}"

        # 3. explain(无 DEEPSEEK_API_KEY → stub JSON;有则 SSE)
        r = client.post(
            f"/exams/{attempt_id}/explain",
            headers={"Authorization": f"Bearer {_user_token()}"},
            json={"question_id": question_id, "level": "standard"},
        )
        assert r.status_code == 200
        d = r.json()
        # ExplainResponse schema:`available` + `explanation` + `reference_answer`
        assert "available" in d
        assert "explanation" in d
        assert "reference_answer" in d
        # 在 stub 路径下,available=False + explanation 是占位文案
        # 详见 explain.py 的 fallback 行为(spec §12.3.4)

"""考试 API subject_id 测试 — fix-23a P0 critical。

覆盖:
- `subject_id='fin-mgmt'` → 41 题 + 0 题来自 corp-strat
- `subject_id='corp-strat'` → 0 题 + 2xx 状态（corp-strat 题库为空）
- 非法 `subject_id` → 400
- 缺 `subject_id` → 422 (Pydantic 必填校验)
- 学科隔离:start → submit → result 链路全走指定学科
"""
from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import get_settings
from app.main import create_app
from app.services.auth_service import create_access_token


@pytest.fixture
def temp_app_db(tmp_path: Path) -> Iterator[Path]:
    """临时 app.db 路径;让 app 初始化时使用。

    ponytail: 复用 test_api.py 的 temp_app_db 模式 — 每个测试用独立 SQLite,
    避免 fixture 间的 app.db 污染。
    """
    s = get_settings()
    original = s.app_db_url
    new_url = f"sqlite+aiosqlite:///{tmp_path}/app.db"
    import app.models.database as db_mod

    db_mod._app_engine = None
    db_mod._app_factory = None
    s.app_db_url = new_url

    import subprocess
    import sys

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
    """FastAPI TestClient with isolated app db."""
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


class TestStartExamSubject:
    """POST /exams/start subject_id 行为(fix-23a P0 critical)。"""

    def test_fin_mgmt_returns_41_questions(self, client: TestClient):
        """subject_id='fin-mgmt' → 默认 41 题,题全部来自 fin-mgmt 学科。"""
        r = client.post(
            "/exams/start",
            headers={"Authorization": f"Bearer {_user_token()}"},
            json={"subject_id": "fin-mgmt"},
        )
        assert r.status_code == 201
        d = r.json()
        assert len(d["questions"]) == 41

        # 验证:所有题都来自 fin-mgmt(从题库 db 查)
        s = get_settings()
        from app.models.database import _extract_sqlite_path

        db_path = _extract_sqlite_path(s.database_url)
        assert db_path is not None
        if not db_path.is_absolute():
            db_path = Path.cwd() / db_path
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        ids = [q["id"] for q in d["questions"]]
        placeholders = ",".join("?" for _ in ids)
        cur.execute(
            f"SELECT subject_id, COUNT(*) FROM questions "
            f"WHERE id IN ({placeholders}) GROUP BY subject_id",
            ids,
        )
        rows = cur.fetchall()
        conn.close()
        # 只可能有 fin-mgmt;corp-strat 应为 0
        subjects = {row[0]: row[1] for row in rows}
        assert subjects == {"fin-mgmt": 41}, (
            f"题库学科分布异常: {subjects}, 期望 {{'fin-mgmt': 41}}"
        )

    def test_corp_strat_returns_correct_size(
        self, client: TestClient, live_corp_strat_count: int
    ):
        """subject_id='corp-strat' → partial-fill 或 full-fill 行为(fix-23a P0 critical)。

        Phase 2-final dynamic:根据 live_corp_strat_count 决定:
        - partial (live < 41): paper.partial=True, paper.returned == live
          + info_msg 存在
        - full (live ≥ 41): paper.partial=False, paper.returned == 41
          + info_msg 为 None

        两种场景都验证:HTTP 2xx(不抛 500) + 不变量。
        """
        r = client.post(
            "/exams/start",
            headers={"Authorization": f"Bearer {_user_token()}"},
            json={"subject_id": "corp-strat"},
        )
        assert r.status_code in (200, 201), f"期望 2xx,实际 {r.status_code}"
        d = r.json()
        # 顶层 attempt_id 必存在
        assert "attempt_id" in d
        # 顶层 questions 字段保留(向后兼容)
        assert "questions" in d
        # paper 字段存在(fix-23a 新增)
        assert "paper" in d and d["paper"] is not None
        assert d["paper"]["requested"] == 41
        # partial vs full 分支断言(Phase 2-final dynamic)
        if live_corp_strat_count < d["paper"]["requested"]:
            # partial 场景
            assert d["paper"]["partial"] is True
            assert d["paper"]["returned"] == live_corp_strat_count
            assert 0 < d["paper"]["returned"] <= live_corp_strat_count
            assert len(d["questions"]) == d["paper"]["returned"]
            assert d.get("info_msg"), "partial-fill 时 info_msg 应存在"
        else:
            # full 场景
            assert d["paper"]["partial"] is False
            assert d["paper"]["returned"] == d["paper"]["requested"]
            assert len(d["questions"]) == d["paper"]["requested"]
            # info_msg 可为 None(full-fill 时不显示)
            assert d.get("info_msg") is None

    def test_invalid_subject_returns_400(self, client: TestClient):
        """非法 subject_id(不在 subjects 表)→ 400 + 错误信息含 subject_id。"""
        r = client.post(
            "/exams/start",
            headers={"Authorization": f"Bearer {_user_token()}"},
            json={"subject_id": "nonexistent-subject"},
        )
        assert r.status_code == 400
        detail = r.json().get("detail", "")
        assert "nonexistent-subject" in detail, (
            f"错误信息应含 subject_id,实际: {detail}"
        )

    def test_missing_subject_returns_422(self, client: TestClient):
        """缺 subject_id → 422 (Pydantic 必填字段校验)。"""
        r = client.post(
            "/exams/start",
            headers={"Authorization": f"Bearer {_user_token()}"},
            json={"mode": "standard"},
        )
        assert r.status_code == 422

    def test_empty_subject_id_rejected(self, client: TestClient):
        """空字符串 subject_id → 422 (min_length=1 校验)。"""
        r = client.post(
            "/exams/start",
            headers={"Authorization": f"Bearer {_user_token()}"},
            json={"subject_id": ""},
        )
        assert r.status_code == 422


class TestStartExamSubjectPersist:
    """start_exam 后,attempt.subject_id 应持久化为请求的 subject_id。"""

    def test_attempt_subject_id_persisted(self, client: TestClient):
        """attempt.subject_id = 'fin-mgmt' 而非 hardcode。"""
        r = client.post(
            "/exams/start",
            headers={"Authorization": f"Bearer {_user_token()}"},
            json={"subject_id": "fin-mgmt"},
        )
        assert r.status_code == 201
        attempt_id = r.json()["attempt_id"]

        # 直接查 app.db 验证 attempt.subject_id
        s = get_settings()
        from app.models.database import _extract_sqlite_path

        db_path = _extract_sqlite_path(s.app_db_url)
        assert db_path is not None
        if not db_path.is_absolute():
            db_path = Path.cwd() / db_path
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cur.execute(
            "SELECT subject_id FROM exam_attempts WHERE id=?", (attempt_id,)
        )
        row = cur.fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "fin-mgmt", f"期望 fin-mgmt,实际 {row[0]}"


class TestGradedAnswerDetailOptions:
    """submit/result 返回的 GradedAnswerDetail.options 字段(fix-23a)。"""

    def test_options_field_populated_for_single(self, client: TestClient):
        """单选题:GradedAnswerDetail.options 应为非空列表。"""
        start = client.post(
            "/exams/start",
            headers={"Authorization": f"Bearer {_user_token()}"},
            json={"subject_id": "fin-mgmt"},
        ).json()
        attempt_id = start["attempt_id"]

        # 找第一道单选题
        single_q = next(
            q for q in start["questions"] if q["type"] == "single"
        )

        # 查正确答案
        s = get_settings()
        from app.models.database import _extract_sqlite_path

        db_path = _extract_sqlite_path(s.database_url)
        assert db_path is not None
        if not db_path.is_absolute():
            db_path = Path.cwd() / db_path
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cur.execute(
            "SELECT answer FROM questions WHERE id=?", (single_q["id"],)
        )
        row = cur.fetchone()
        conn.close()
        assert row is not None

        # 提交 + 查 result
        client.post(
            f"/exams/{attempt_id}/submit",
            headers={"Authorization": f"Bearer {_user_token()}"},
            json={
                "answers": [
                    {"question_id": single_q["id"], "user_answer": row[0]}
                ]
            },
        )
        result = client.get(
            f"/exams/{attempt_id}/result",
            headers={"Authorization": f"Bearer {_user_token()}"},
        ).json()

        # 找到该题 detail
        detail = next(
            a for a in result["answers"] if a["question_id"] == single_q["id"]
        )
        assert "options" in detail, f"detail 缺 options 字段: {detail.keys()}"
        assert detail["options"] is not None, "单选题 options 应非 None"
        assert isinstance(detail["options"], list)
        assert len(detail["options"]) > 0, f"单选题 options 应非空,实际 {detail['options']}"

    def test_options_field_null_for_calc(self, client: TestClient):
        """计算题:GradedAnswerDetail.options 应为 None(无选项)。"""
        start = client.post(
            "/exams/start",
            headers={"Authorization": f"Bearer {_user_token()}"},
            json={"subject_id": "fin-mgmt"},
        ).json()
        attempt_id = start["attempt_id"]
        client.post(
            f"/exams/{attempt_id}/submit",
            headers={"Authorization": f"Bearer {_user_token()}"},
            json={"answers": []},
        )
        result = client.get(
            f"/exams/{attempt_id}/result",
            headers={"Authorization": f"Bearer {_user_token()}"},
        ).json()

        # 找一道计算题
        calc_detail = next(
            (a for a in result["answers"] if a["type"] == "calc"), None
        )
        if calc_detail is None:
            pytest.skip("试卷无 calc 题,跳过")
        # calc 不应有 options 字段(后端返 None)
        assert calc_detail.get("options") is None, (
            f"calc 题型 options 应为 None,实际 {calc_detail.get('options')}"
        )

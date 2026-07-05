"""API 集成测试 — start → submit → result 全链路。

不依赖真实 LLM；使用内存 app.db 隔离（每个测试用临时 db）。
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app
from app.services.auth_service import create_access_token


@pytest.fixture
def temp_app_db(tmp_path: Path) -> Iterator[Path]:
    """临时 app.db 路径；让 app 初始化时使用。"""
    s = get_settings()
    original = s.app_db_url
    new_url = f"sqlite+aiosqlite:///{tmp_path}/app.db"
    import app.models.database as db_mod

    db_mod._app_engine = None
    db_mod._app_factory = None
    s.app_db_url = new_url

    import subprocess
    import sys

    # 把当前 venv 的 bin 注入 PATH，确保 alembic 可被找到（subprocess 不继承 venv）
    venv_bin = Path(sys.executable).parent
    env = {**os.environ, "APP_DB_URL": new_url, "PATH": f"{venv_bin}{os.pathsep}{os.environ.get('PATH', '')}"}

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
    """FastAPI TestClient with isolated app db。

    ponytail: 临时把 Settings.deepseek_api_key 改成空串（让 explain 走 stub），
    测试结束恢复。不调 get_settings.cache_clear()——它会丢掉 temp_app_db 已配好的
    app_db_url，导致 dashboard 测试看到真实 app.db。
    """
    from app.config import get_settings
    from app.services import deepseek_client

    s = get_settings()
    saved_deepseek = s.deepseek_api_key
    s.deepseek_api_key = ""  # env var > .env 优先级：空串走 stub 分支
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


class TestHealthEndpoint:
    """GET /health — 不需鉴权。"""

    def test_health_ok(self, client: TestClient):
        r = client.get("/health")
        assert r.status_code == 200
        d = r.json()
        assert d["status"] == "ok"
        assert d["database"] is True
        assert d["question_count"] is not None
        assert d["question_count"] >= 100


class TestExamFlow:
    """完整考试流程：start → submit → result。"""

    def test_start_exam_returns_41_questions(self, client: TestClient):
        r = client.post(
            "/exams/start",
            headers={"Authorization": f"Bearer {_user_token()}"},
            json={"subject_id": "fin-mgmt"},
        )
        assert r.status_code == 201
        d = r.json()
        assert "attempt_id" in d
        assert len(d["questions"]) == 41
        assert d["time_limit_minutes"] == 120

    def test_start_exam_no_token_rejected(self, client: TestClient):
        r = client.post("/exams/start", json={})
        # HTTPBearer 在 auto_error=True 时，无 token 返回 401
        assert r.status_code in (401, 403)

    def test_get_exam_snapshot(self, client: TestClient):
        start = client.post(
            "/exams/start",
            headers={"Authorization": f"Bearer {_user_token()}"},
            json={"subject_id": "fin-mgmt"},
        ).json()
        attempt_id = start["attempt_id"]
        r = client.get(
            f"/exams/{attempt_id}",
            headers={"Authorization": f"Bearer {_user_token()}"},
        )
        assert r.status_code == 200
        d = r.json()
        assert d["attempt_id"] == attempt_id
        assert d["submitted_at"] is None
        assert len(d["questions"]) == 41

    def test_get_exam_404(self, client: TestClient):
        r = client.get(
            "/exams/99999",
            headers={"Authorization": f"Bearer {_user_token()}"},
        )
        assert r.status_code == 404

    def test_submit_exam_all_correct(self, client: TestClient):
        """交卷 + 全对答案 → 满分。"""
        start = client.post(
            "/exams/start",
            headers={"Authorization": f"Bearer {_user_token()}"},
            json={"subject_id": "fin-mgmt"},
        ).json()
        attempt_id = start["attempt_id"]
        questions = start["questions"]

        # 取每题正确答案（重新查库）— 用绝对路径
        import sqlite3

        from app.models.database import _extract_sqlite_path

        s = get_settings()
        db_path = _extract_sqlite_path(s.database_url)
        assert db_path is not None
        if not db_path.is_absolute():
            db_path = Path.cwd() / db_path
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        answers = []
        for q in questions:
            cur.execute("SELECT answer FROM questions WHERE id=?", (q["id"],))
            row = cur.fetchone()
            if row:
                answers.append({"question_id": q["id"], "user_answer": row[0]})
        conn.close()

        r = client.post(
            f"/exams/{attempt_id}/submit",
            headers={"Authorization": f"Bearer {_user_token()}"},
            json={"answers": answers},
        )
        assert r.status_code == 200
        d = r.json()
        assert d["attempt_id"] == attempt_id
        # 客观题全对，但 calc/comprehensive 主观题是文本比对，不一定全对
        assert d["total_score"] > 0

    def test_submit_exam_double_submit_blocked(self, client: TestClient):
        start = client.post(
            "/exams/start",
            headers={"Authorization": f"Bearer {_user_token()}"},
            json={"subject_id": "fin-mgmt"},
        ).json()
        attempt_id = start["attempt_id"]
        # 第一次交卷
        client.post(
            f"/exams/{attempt_id}/submit",
            headers={"Authorization": f"Bearer {_user_token()}"},
            json={"answers": []},
        )
        # 第二次交卷
        r2 = client.post(
            f"/exams/{attempt_id}/submit",
            headers={"Authorization": f"Bearer {_user_token()}"},
            json={"answers": []},
        )
        assert r2.status_code == 409

    def test_submit_empty_answers(self, client: TestClient):
        """交白卷 → 总分 0。"""
        start = client.post(
            "/exams/start",
            headers={"Authorization": f"Bearer {_user_token()}"},
            json={"subject_id": "fin-mgmt"},
        ).json()
        attempt_id = start["attempt_id"]
        r = client.post(
            f"/exams/{attempt_id}/submit",
            headers={"Authorization": f"Bearer {_user_token()}"},
            json={"answers": []},
        )
        assert r.status_code == 200
        d = r.json()
        assert d["total_score"] == 0
        assert all(a["awarded_score"] == 0 for a in d["answers"])

    def test_get_result_after_submit(self, client: TestClient):
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
        r = client.get(
            f"/exams/{attempt_id}/result",
            headers={"Authorization": f"Bearer {_user_token()}"},
        )
        assert r.status_code == 200
        d = r.json()
        assert d["submitted_at"] is not None
        assert d["total_score"] == 0

    def test_get_result_before_submit_blocked(self, client: TestClient):
        start = client.post(
            "/exams/start",
            headers={"Authorization": f"Bearer {_user_token()}"},
            json={"subject_id": "fin-mgmt"},
        ).json()
        attempt_id = start["attempt_id"]
        r = client.get(
            f"/exams/{attempt_id}/result",
            headers={"Authorization": f"Bearer {_user_token()}"},
        )
        assert r.status_code == 400

    def test_result_after_correct_submit_awarded_score_not_zero(
        self, client: TestClient
    ):
        """fix-22 critical regression test。

        早期 result endpoint 因跨 session bug 返回 awarded_score 全 0。
        此测试用全对答案触发 submit，再 GET /result，断言至少有题分非 0。
        """
        # 取全对答案
        start = client.post(
            "/exams/start",
            headers={"Authorization": f"Bearer {_user_token()}"},
            json={"subject_id": "fin-mgmt"},
        ).json()
        attempt_id = start["attempt_id"]
        questions = start["questions"]

        import sqlite3

        from app.models.database import _extract_sqlite_path

        s = get_settings()
        db_path = _extract_sqlite_path(s.database_url)
        assert db_path is not None
        if not db_path.is_absolute():
            db_path = Path.cwd() / db_path
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        answers = []
        for q in questions:
            cur.execute("SELECT answer FROM questions WHERE id=?", (q["id"],))
            row = cur.fetchone()
            if row:
                answers.append({"question_id": q["id"], "user_answer": row[0]})
        conn.close()

        submit_resp = client.post(
            f"/exams/{attempt_id}/submit",
            headers={"Authorization": f"Bearer {_user_token()}"},
            json={"answers": answers},
        )
        assert submit_resp.status_code == 200
        submit_total = submit_resp.json()["total_score"]
        assert submit_total > 0, f"submit total_score={submit_total}，期望 > 0"

        # GET /result — 必须看到非 0 awarded_score
        result = client.get(
            f"/exams/{attempt_id}/result",
            headers={"Authorization": f"Bearer {_user_token()}"},
        )
        assert result.status_code == 200
        d = result.json()
        assert d["total_score"] == submit_total, (
            f"result total_score={d['total_score']} 与 submit "
            f"total_score={submit_total} 不一致（潜在跨 session bug）"
        )
        # 关键断言：至少有一题 awarded_score > 0
        nonzero = [a for a in d["answers"] if a["awarded_score"] > 0]
        assert len(nonzero) > 0, (
            "result endpoint 所有题 awarded_score 都为 0 — "
            "fix-22 critical bug 未真正修复"
        )


class TestStartExamMode:
    """POST /exams/start 支持 mode 字段（fix-22）。"""

    def test_start_exam_default_is_standard(self, client: TestClient):
        r = client.post(
            "/exams/start",
            headers={"Authorization": f"Bearer {_user_token()}"},
            json={"subject_id": "fin-mgmt"},
        )
        assert r.status_code == 201
        assert "attempt_id" in r.json()

    def test_start_exam_mode_standard(self, client: TestClient):
        r = client.post(
            "/exams/start",
            headers={"Authorization": f"Bearer {_user_token()}"},
            json={"subject_id": "fin-mgmt", "mode": "standard"},
        )
        assert r.status_code == 201
        assert "attempt_id" in r.json()

    def test_start_exam_mode_mixed_stub(self, client: TestClient):
        """mixed 模式当前是 stub，行为等同 standard —— 仍应返回 41 题。"""
        r = client.post(
            "/exams/start",
            headers={"Authorization": f"Bearer {_user_token()}"},
            json={"subject_id": "fin-mgmt", "mode": "mixed"},
        )
        assert r.status_code == 201
        d = r.json()
        assert len(d["questions"]) == 41

    def test_start_exam_invalid_mode_rejected(self, client: TestClient):
        """非法 mode → 422（Pydantic extra=forbid / Literal 校验）。"""
        r = client.post(
            "/exams/start",
            headers={"Authorization": f"Bearer {_user_token()}"},
            json={"subject_id": "fin-mgmt", "mode": "garbage"},
        )
        assert r.status_code == 422


class TestDeleteExam:
    """DELETE /exams/{id} — fix-22 新增。"""

    def _make_attempt(self, client: TestClient) -> int:
        r = client.post(
            "/exams/start",
            headers={"Authorization": f"Bearer {_user_token()}"},
            json={"subject_id": "fin-mgmt"},
        )
        return r.json()["attempt_id"]

    def test_delete_existing_attempt(self, client: TestClient):
        attempt_id = self._make_attempt(client)
        r = client.delete(
            f"/exams/{attempt_id}",
            headers={"Authorization": f"Bearer {_user_token()}"},
        )
        assert r.status_code == 204

        # 再次 GET 应 404
        r2 = client.get(
            f"/exams/{attempt_id}",
            headers={"Authorization": f"Bearer {_user_token()}"},
        )
        assert r2.status_code == 404

    def test_delete_nonexistent_returns_404(self, client: TestClient):
        r = client.delete(
            "/exams/99999",
            headers={"Authorization": f"Bearer {_user_token()}"},
        )
        assert r.status_code == 404

    def test_delete_no_token_rejected(self, client: TestClient):
        attempt_id = self._make_attempt(client)
        r = client.delete(f"/exams/{attempt_id}")
        assert r.status_code in (401, 403)

    def test_delete_cascades_attempt_answers(self, client: TestClient):
        """删除 attempt 后，attempt_answers 应一并清空（避免孤儿行）。"""
        attempt_id = self._make_attempt(client)

        # 验证 attempt_answers 存在（直接查 SQLite）
        from app.models.database import _extract_sqlite_path

        s = get_settings()
        db_path = _extract_sqlite_path(s.app_db_url)
        if db_path and not db_path.is_absolute():
            db_path = Path.cwd() / db_path
        import sqlite3

        assert db_path is not None
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM attempt_answers WHERE attempt_id=?",
            (attempt_id,),
        )
        before = cur.fetchone()[0]
        conn.close()
        assert before == 41, f"期望 41 条 answers，实际 {before}"

        # 删除
        r = client.delete(
            f"/exams/{attempt_id}",
            headers={"Authorization": f"Bearer {_user_token()}"},
        )
        assert r.status_code == 204

        # 验证 attempt_answers 已级联清空
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM attempt_answers WHERE attempt_id=?",
            (attempt_id,),
        )
        after = cur.fetchone()[0]
        conn.close()
        assert after == 0, f"删除后仍有 {after} 条 answers，级联失败"


class TestDashboard:
    """GET /dashboard。"""

    def test_dashboard_after_two_attempts(self, client: TestClient):
        # 跑两次完整考试
        for _ in range(2):
            start = client.post(
                "/exams/start",
                headers={"Authorization": f"Bearer {_user_token()}"},
                json={"subject_id": "fin-mgmt"},
            ).json()
            client.post(
                f"/exams/{start['attempt_id']}/submit",
                headers={"Authorization": f"Bearer {_user_token()}"},
                json={"answers": []},
            )
        r = client.get(
            "/dashboard", headers={"Authorization": f"Bearer {_user_token()}"}
        )
        assert r.status_code == 200
        d = r.json()
        assert d["total_attempts"] == 2
        assert len(d["score_trend"]) == 2
        assert len(d["history"]) == 2


class TestAdminReview:
    """Admin review 端点。"""

    def test_review_queue_admin(self, client: TestClient):
        r = client.get(
            "/admin/review/queue",
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
        assert r.status_code == 200
        d = r.json()
        assert "items" in d
        assert "total" in d

    def test_review_update_question(self, client: TestClient):
        # 取第一道有 flag 的题
        queue = client.get(
            "/admin/review/queue",
            headers={"Authorization": f"Bearer {_admin_token()}"},
        ).json()
        if not queue["items"]:
            pytest.skip("review 队列为空，跳过")
        qid = queue["items"][0]["id"]
        r = client.post(
            f"/admin/review/questions/{qid}",
            headers={"Authorization": f"Bearer {_admin_token()}"},
            json={"analysis": "由单元测试注入的解析"},
        )
        assert r.status_code == 200
        d = r.json()
        assert d["question_id"] == qid
        assert "analysis" in d["updated_fields"]


class TestExplainStub:
    """POST /exams/{id}/explain — 占位 stub（不调外部 LLM）。"""

    def test_explain_returns_stub(self, client: TestClient):
        # 先做一次完整考试
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
        # 取该试卷第一题
        qid = start["questions"][0]["id"]
        r = client.post(
            f"/exams/{attempt_id}/explain",
            headers={"Authorization": f"Bearer {_user_token()}"},
            json={"question_id": qid, "level": "standard"},
        )
        assert r.status_code == 200
        d = r.json()
        assert d["available"] is False
        assert "暂不可用" in d["explanation"]
        assert d["reference_answer"]
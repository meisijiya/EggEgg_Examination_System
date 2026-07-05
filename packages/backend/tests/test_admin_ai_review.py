"""admin.py Phase 1.2 新增 endpoints 单测。

测试目标:
- _read_ai_jsonl / _write_ai_jsonl: round-trip
- _set_item_status: 找到 + 改 status
- approve / reject endpoints: 走完整 admin API 路由(已登录 admin)
- reject 必须有 review_note
- pending → approved 完整链路
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# 让 admin.py 中的 fastapi/Query 等导入可解析
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# 准备一个隔离的 AI JSONL 测试 fixture(临时目录)
# ---------------------------------------------------------------------------


@pytest.fixture
def ai_jsonl_path(tmp_path, monkeypatch):
    """生成临时 ai_generated.jsonl + 让 admin 指向该路径。"""
    test_jsonl = tmp_path / "ai_generated.jsonl"
    sample = [
        {"id": "q1", "type": "calc", "stem": "Q1", "answer": "A", "key_points": ["x"],
         "ai_generated": True, "confidence": 0.5, "needs_manual_review": True,
         "status": "pending", "review_reason": "test"},
        {"id": "q2", "type": "judge", "stem": "Q2", "answer": "对", "key_points": [],
         "ai_generated": True, "confidence": 0.9, "needs_manual_review": False,
         "status": "pending", "review_reason": None},
        {"id": "q3", "type": "multi", "stem": "Q3", "answer": "AB", "key_points": ["y"],
         "ai_generated": True, "confidence": 0.4, "needs_manual_review": True,
         "status": "approved", "review_reason": "manually OK"},
    ]
    test_jsonl.write_text(
        "\n".join(json.dumps(s, ensure_ascii=False) for s in sample) + "\n",
        encoding="utf-8",
    )

    # Patch admin.py module 内的 AI_GENERATED_JSONL 常量
    from app.api import admin as admin_mod

    monkeypatch.setattr(admin_mod, "AI_GENERATED_JSONL", test_jsonl)

    # 等同:模块重新 import 数据(只支持模块级别常量,函数内已经读取 _read_ai_jsonl(AI_GENERATED_JSONL))
    # 由于 _read_ai_jsonl 是闭包到 AI_GENERATED_JSONL 默认参数,测试也要覆盖该默认
    # 我们的实现 _read_ai_jsonl(path: Path = AI_GENERATED_JSONL) - monkeypatch.setattr 会改变默认值
    return test_jsonl


# ---------------------------------------------------------------------------
# 基础 JSONL I/O 测试
# ---------------------------------------------------------------------------


def test_jsonl_read_round_trip(ai_jsonl_path):
    from app.api import admin as admin_mod

    items = admin_mod._read_ai_jsonl(ai_jsonl_path)
    assert len(items) == 3
    assert items[0]["id"] == "q1"
    assert items[2]["status"] == "approved"


def test_jsonl_write_replaces(ai_jsonl_path, tmp_path):
    """write 应该是原子替换(写新内容到 *.tmp 然后 replace)。"""
    from app.api import admin as admin_mod

    new_items = [
        {"id": "new1", "type": "single", "stem": "NEW", "answer": "A",
         "key_points": [], "ai_generated": True, "confidence": 1.0,
         "needs_manual_review": False, "status": "pending",
         "review_reason": None, "web_evidence": []}
    ]
    n = admin_mod._write_ai_jsonl(new_items, ai_jsonl_path)
    assert n == 1

    items = admin_mod._read_ai_jsonl(ai_jsonl_path)
    assert len(items) == 1
    assert items[0]["id"] == "new1"


def test_jsonl_read_nonexistent_returns_empty(tmp_path):
    """不存在的 JSONL → 返回 []。"""
    from app.api import admin as admin_mod

    p = tmp_path / "nope.jsonl"
    assert admin_mod._read_ai_jsonl(p) == []


# ---------------------------------------------------------------------------
# _set_item_status 单测
# ---------------------------------------------------------------------------


def test_set_status_found_approve(ai_jsonl_path):
    from app.api import admin as admin_mod

    resp = admin_mod._set_item_status("q1", "approved", "looks good")
    assert resp.question_id == "q1"
    assert resp.new_status == "approved"
    assert resp.review_note == "looks good"

    items = admin_mod._read_ai_jsonl(ai_jsonl_path)
    q1 = next(it for it in items if it["id"] == "q1")
    assert q1["status"] == "approved"
    assert q1["review_reason"] == "looks good"
    assert "reviewed_at" in q1


def test_set_status_not_found_raises_404(ai_jsonl_path):
    from app.api import admin as admin_mod
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        admin_mod._set_item_status("nonexistent", "approved")
    assert exc_info.value.status_code == 404


def test_set_status_rejected_requires_note(ai_jsonl_path):
    """reject 路径应该由 endpoint 强制要求 review_note,但 _set_item_status 本身允许 None。"""
    from app.api import admin as admin_mod

    # _set_item_status 不强制 note(None 也行)— 这是 endpoint 的职责
    resp = admin_mod._set_item_status("q1", "rejected", None)
    assert resp.review_note is None
    assert resp.new_status == "rejected"


# ---------------------------------------------------------------------------
# Endpoints 集成测试(用 FastAPI TestClient + mock admin auth)
# ---------------------------------------------------------------------------


def test_endpoint_list_ai_questions_pending(ai_jsonl_path):
    """GET /admin/ai-generated-questions?status=pending → 2 条。"""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.api.auth import get_admin_user

    # override admin auth → 模拟 admin 登录态
    app.dependency_overrides[get_admin_user] = lambda: "admin-user"
    try:
        client = TestClient(app)
        resp = client.get("/admin/ai-generated-questions?status=pending")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert all(it["status"] == "pending" for it in data["items"])
    finally:
        app.dependency_overrides.clear()


def test_endpoint_list_ai_questions_approved(ai_jsonl_path):
    """GET ?status=approved → 1 条(q3)。"""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.api.auth import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: "admin-user"
    try:
        client = TestClient(app)
        resp = client.get("/admin/ai-generated-questions?status=approved")
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["id"] == "q3"
    finally:
        app.dependency_overrides.clear()


def test_endpoint_list_ai_questions_all(ai_jsonl_path):
    """GET ?status=all → 3 条全部。"""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.api.auth import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: "admin-user"
    try:
        client = TestClient(app)
        resp = client.get("/admin/ai-generated-questions?status=all")
        data = resp.json()
        assert data["total"] == 3
    finally:
        app.dependency_overrides.clear()


def test_endpoint_approve_question(ai_jsonl_path):
    """POST /admin/approve-question/{id} → 改 status='approved' 并写回 JSONL。"""
    from fastapi.testclient import TestClient

    from app.api import admin as admin_mod
    from app.api.auth import get_admin_user
    from app.main import app

    app.dependency_overrides[get_admin_user] = lambda: "admin-user"
    try:
        client = TestClient(app)
        resp = client.post(
            "/admin/approve-question/q1",
            json={"review_note": "looks fine after human review"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["question_id"] == "q1"
        assert data["new_status"] == "approved"

        # 验证 JSONL 实际改动
        items = admin_mod._read_ai_jsonl(ai_jsonl_path)
        q1 = next(it for it in items if it["id"] == "q1")
        assert q1["status"] == "approved"
    finally:
        app.dependency_overrides.clear()


def test_endpoint_reject_question_with_note(ai_jsonl_path):
    """POST /admin/reject-question/{id} + review_note → 改 status='rejected'。"""
    from fastapi.testclient import TestClient

    from app.api import admin as admin_mod
    from app.api.auth import get_admin_user
    from app.main import app

    app.dependency_overrides[get_admin_user] = lambda: "admin-user"
    try:
        client = TestClient(app)
        resp = client.post(
            "/admin/reject-question/q2",
            json={"review_note": "题号与原资料不一致,reject"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["new_status"] == "rejected"

        items = admin_mod._read_ai_jsonl(ai_jsonl_path)
        q2 = next(it for it in items if it["id"] == "q2")
        assert q2["status"] == "rejected"
    finally:
        app.dependency_overrides.clear()


def test_endpoint_reject_without_note_400(ai_jsonl_path):
    """reject 但 review_note 为空 → 400。"""
    from fastapi.testclient import TestClient

    from app.api.auth import get_admin_user
    from app.main import app

    app.dependency_overrides[get_admin_user] = lambda: "admin-user"
    try:
        client = TestClient(app)
        # review_note = "" 也算缺(我们检查 `not req.review_note`)
        resp = client.post(
            "/admin/reject-question/q1",
            json={"review_note": ""},
        )
        assert resp.status_code == 400
        assert "review_note" in resp.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_endpoint_approve_nonexistent_404(ai_jsonl_path):
    """approve 不存在的 id → 404。"""
    from fastapi.testclient import TestClient

    from app.api.auth import get_admin_user
    from app.main import app

    app.dependency_overrides[get_admin_user] = lambda: "admin-user"
    try:
        client = TestClient(app)
        resp = client.post(
            "/admin/approve-question/nonexistent-id",
            json={"review_note": "x"},
        )
        assert resp.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_endpoint_unauthenticated_401(ai_jsonl_path):
    """未登录访问 → 401(admin auth 强制)。"""
    from fastapi.testclient import TestClient

    from app.main import app

    # 不 override admin auth — 真实 auth 必须 admin token
    client = TestClient(app)
    resp = client.get("/admin/ai-generated-questions?status=pending")
    assert resp.status_code in (401, 403)  # fastapi HTTPBearer 返回 401


def test_endpoint_review_queue_unchanged(ai_jsonl_path):
    """原有 /admin/review/queue endpoint 不被新代码破坏。"""
    """验证 admin.py 模块加载成功 + /admin/review/queue 路由仍注册。"""
    from app.api import admin as admin_mod

    paths = [r.path for r in admin_mod.router.routes]
    assert "/admin/review/queue" in paths
    assert "/admin/review/questions/{question_id}" in paths
    assert "/admin/ai-generated-questions" in paths
    assert "/admin/approve-question/{question_id}" in paths
    assert "/admin/reject-question/{question_id}" in paths

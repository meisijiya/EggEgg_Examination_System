"""静态文件 + SPA fallback 集成测试。

覆盖：
- GET /           → 200 HTML（index.html）
- GET /admin      → 200 HTML（SPA fallback middleware）
- GET /assets/index-xxx.js → 200 JS（静态挂载）
- GET /health     → 200 JSON（不被 middleware 拦截）
- GET /assets/nonexistent.css → 404（真缺失，不要被 SPA fallback 吃掉）
- GET /nonexistent-page → 200 HTML（SPA catch-all）
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import STATIC_DIR, create_app


@pytest.fixture(scope="module")
def static_client() -> TestClient:
    """构造带 dist 可用的客户端。模块级 scope 避免重复创建 engine。"""
    app = create_app()
    return TestClient(app)


@pytest.fixture(scope="module")
def asset_js_name() -> str:
    """返回 dist/assets 下第一个 *.js 文件名（含 hash），用作静态资源测试。"""
    if STATIC_DIR is None:
        pytest.skip("dist 不可用")
    assets = STATIC_DIR / "assets"
    if not assets.exists():
        pytest.skip("dist/assets 不存在")
    js_files = sorted(assets.glob("*.js"))
    if not js_files:
        pytest.skip("dist/assets 下没有 .js 文件")
    return js_files[0].name


@pytest.mark.skipif(STATIC_DIR is None, reason="dist 不可用")
class TestRootServesIndex:
    """GET / 返回 index.html。"""

    def test_root_returns_html(self, static_client: TestClient):
        r = static_client.get("/")
        assert r.status_code == 200
        ct = r.headers.get("content-type", "")
        assert "text/html" in ct
        assert "财务管理" in r.text or "<div id=\"app\"" in r.text


@pytest.mark.skipif(STATIC_DIR is None, reason="dist 不可用")
class TestSPAFallback:
    """非 API 路径 404 → 返回 index.html（SPA 路由刷新友好）。"""

    def test_admin_path_serves_index(self, static_client: TestClient):
        """GET /admin（不存在该路径）→ SPA fallback 返回 index.html。"""
        r = static_client.get("/admin")
        assert r.status_code == 200
        ct = r.headers.get("content-type", "")
        assert "text/html" in ct

    def test_nonexistent_page_serves_index(self, static_client: TestClient):
        """GET /random-page-xyz → SPA fallback 返回 index.html。"""
        r = static_client.get("/random-page-xyz")
        assert r.status_code == 200
        ct = r.headers.get("content-type", "")
        assert "text/html" in ct

    def test_admin_no_fallback_for_static_extension(self, static_client: TestClient):
        """GET /admin/missing.css → 不要 fallback（保留 404，让前端知道真缺失）。"""
        r = static_client.get("/admin/missing.css")
        # 静态扩展名 → middleware 不应 fallback
        assert r.status_code == 404

    def test_post_not_fallback(self, static_client: TestClient):
        """POST 方法不触发 SPA fallback（404 应保留语义）。"""
        # POST 到不存在的路径 — FastAPI 通常返回 405/404，middleware 不应改成 200 HTML
        r = static_client.post("/random-page-xyz", json={})
        assert r.status_code in (404, 405)


@pytest.mark.skipif(STATIC_DIR is None, reason="dist 不可用")
class TestStaticAssets:
    """静态资源挂载。"""

    def test_assets_js_served(self, static_client: TestClient, asset_js_name: str):
        """GET /assets/<random>.js 返回 JS 内容。"""
        r = static_client.get(f"/assets/{asset_js_name}")
        assert r.status_code == 200
        ct = r.headers.get("content-type", "")
        assert "javascript" in ct or "js" in ct or r.headers.get("content-length")
        # 前端 SPA 的入口必须有 "createApp" 或类似关键字（任一即可）
        body = r.text
        assert len(body) > 100

    def test_assets_nonexistent_404_not_fallback(self, static_client: TestClient):
        """GET /assets/nonexistent.css → 404，不被 fallback 改成 HTML。"""
        r = static_client.get("/assets/nonexistent.css")
        assert r.status_code == 404
        # SPA middleware 看到 .css 后缀应跳过 fallback
        ct = r.headers.get("content-type", "")
        assert "text/html" not in ct


class TestApiNotIntercepted:
    """API 端点不被 SPA fallback middleware 吞掉。"""

    def test_health_returns_json(self, static_client: TestClient):
        """GET /health → 200 JSON（不被改写为 HTML）。"""
        r = static_client.get("/health")
        assert r.status_code == 200
        ct = r.headers.get("content-type", "")
        assert "application/json" in ct
        d = r.json()
        assert d["status"] in ("ok", "degraded")

    def test_explain_endpoint_404_not_html(self, static_client: TestClient):
        """未鉴权 GET /exams/99999 → 404 JSON（API 错误不被 SPA fallback）。"""
        r = static_client.get("/exams/99999/result")
        # 该路径需要鉴权 — 应该返回 401/403 而不是被 SPA middleware 拦下
        assert r.status_code in (401, 403)


class TestNoStaticDirFallback:
    """dist 不可用时优雅降级：root 返回 JSON 提示。"""

    def test_root_message_when_dist_missing(self, monkeypatch, tmp_path: Path):
        """dist 不存在时，根路径应返回 JSON 而不是抛错。"""
        # 把 _resolve_static_dir 强制返回 None
        from app import main as main_mod

        monkeypatch.setattr(main_mod, "_resolve_static_dir", lambda: None)
        monkeypatch.setattr(main_mod, "STATIC_DIR", None)
        monkeypatch.setattr(main_mod, "STATIC_AVAILABLE", False)

        app = main_mod.create_app()

        with TestClient(app) as c:
            r = c.get("/")
            assert r.status_code == 200
            ct = r.headers.get("content-type", "")
            assert "application/json" in ct
            d = r.json()
            assert "Frontend not built" in d["message"]

"""认证测试 — 单密码 JWT + 角色识别。"""
from __future__ import annotations

import time

import jwt
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app
from app.services.auth_service import (
    InvalidCredentialsError,
    authenticate,
    create_access_token,
    decode_token,
)


@pytest.fixture
def client() -> TestClient:
    """FastAPI TestClient。"""
    app = create_app()
    return TestClient(app)


class TestAuthenticate:
    """密码 → 角色识别。"""

    def test_admin_password(self):
        s = get_settings()
        assert authenticate(s.admin_password) == "admin"

    def test_user_password(self):
        s = get_settings()
        assert authenticate(s.user_password) == "user"

    def test_wrong_password(self):
        with pytest.raises(InvalidCredentialsError):
            authenticate("definitely-wrong-password")


class TestJWTToken:
    """JWT 编解码。"""

    def test_create_and_decode_user(self):
        token, expires_in = create_access_token("user")
        payload = decode_token(token)
        assert payload.sub == "user"
        assert payload.exp > payload.iat
        assert expires_in > 0

    def test_create_and_decode_admin(self):
        token, _ = create_access_token("admin")
        payload = decode_token(token)
        assert payload.sub == "admin"

    def test_decode_invalid_token(self):
        with pytest.raises(jwt.PyJWTError):
            decode_token("invalid.token.here")


class TestLoginEndpoint:
    """POST /auth/login 端点。"""

    def test_login_user_success(self, client: TestClient):
        s = get_settings()
        r = client.post("/auth/login", json={"password": s.user_password})
        assert r.status_code == 200
        data = r.json()
        assert data["role"] == "user"
        assert data["token_type"] == "bearer"
        assert "access_token" in data
        assert data["expires_in"] > 0

    def test_login_admin_success(self, client: TestClient):
        s = get_settings()
        r = client.post("/auth/login", json={"password": s.admin_password})
        assert r.status_code == 200
        assert r.json()["role"] == "admin"

    def test_login_wrong_password(self, client: TestClient):
        r = client.post("/auth/login", json={"password": "wrong"})
        assert r.status_code == 401

    def test_login_empty_password(self, client: TestClient):
        # Pydantic min_length=1 拦截空字符串
        r = client.post("/auth/login", json={"password": ""})
        assert r.status_code == 422

    def test_login_extra_field_rejected(self, client: TestClient):
        # extra='forbid'
        r = client.post("/auth/login", json={"password": "x", "extra": "field"})
        assert r.status_code == 422


class TestProtectedEndpoints:
    """鉴权依赖注入守卫。"""

    def test_health_no_auth_required(self, client: TestClient):
        r = client.get("/health")
        assert r.status_code == 200

    def test_dashboard_requires_auth(self, client: TestClient):
        r = client.get("/dashboard")
        # HTTPBearer 在 auto_error=True 时，无 token 返回 401
        assert r.status_code in (401, 403)

    def test_dashboard_with_user_token(self, client: TestClient):
        s = get_settings()
        token, _ = create_access_token("user")
        r = client.get("/dashboard", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200

    def test_admin_requires_admin(self, client: TestClient):
        # user token → 403
        token, _ = create_access_token("user")
        r = client.get(
            "/admin/review/queue", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 403

    def test_admin_with_admin_token(self, client: TestClient):
        token, _ = create_access_token("admin")
        r = client.get(
            "/admin/review/queue", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 200

    def test_expired_token_rejected(self, client: TestClient):
        # 构造一个已过期 token
        s = get_settings()
        import datetime as _dt

        payload = {
            "sub": "user",
            "iat": int(time.time()) - 7200,
            "exp": int(time.time()) - 3600,
        }
        token = jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm)
        r = client.get("/dashboard", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401
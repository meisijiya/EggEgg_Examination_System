"""认证 API + FastAPI 依赖注入。

- POST /auth/login: 单密码登录，颁发 JWT（区分 USER / ADMIN）
- get_current_user / get_admin_user: 依赖注入守卫
"""
from __future__ import annotations

from typing import Annotated, Literal

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings
from app.schemas import LoginRequest, LoginResponse
from app.services.auth_service import (
    InvalidCredentialsError,
    authenticate,
    create_access_token,
    decode_token,
)

router = APIRouter(prefix="/auth", tags=["auth"])

# 自动登录无需 token，但其它接口要用
_bearer = HTTPBearer(auto_error=True)


def _extract_role(creds: HTTPAuthorizationCredentials) -> Literal["user", "admin"]:
    """从 Bearer token 解析角色，失败抛 401。"""
    try:
        payload = decode_token(creds.credentials)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token 已过期")
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token 无效")
    if payload.sub not in ("user", "admin"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token 角色非法")
    return payload.sub  # type: ignore[return-value]


async def get_current_user(
    creds: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
) -> Literal["user", "admin"]:
    """依赖注入：要求任意已登录用户（user 或 admin）。"""
    return _extract_role(creds)


async def get_admin_user(
    creds: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
) -> Literal["admin"]:
    """依赖注入：要求 admin 角色。"""
    role = _extract_role(creds)
    if role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return role


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest) -> LoginResponse:
    """单密码登录 — 区分 USER / ADMIN。"""
    settings = get_settings()
    try:
        role = authenticate(req.password)
    except InvalidCredentialsError:
        # 不区分"密码错"与"用户不存在"，统一 401
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="密码不正确")

    token, expires_in = create_access_token(role)
    return LoginResponse(access_token=token, role=role, expires_in=expires_in)
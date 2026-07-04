"""认证服务 — 单密码 JWT（USER / ADMIN 双密码）。"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

import jwt

from app.config import get_settings


class InvalidCredentialsError(Exception):
    """认证失败异常。"""

    pass


@dataclass
class TokenPayload:
    """JWT 解码后的载荷。"""

    sub: Literal["user", "admin"]
    iat: int
    exp: int


def authenticate(password: str) -> Literal["user", "admin"]:
    """根据密码匹配角色。

    - USER_PASSWORD → "user"
    - ADMIN_PASSWORD → "admin"
    - 都不匹配 → 抛 InvalidCredentialsError
    """
    settings = get_settings()
    # 使用 secrets.compare_digest 防御时序攻击
    if secrets.compare_digest(password, settings.admin_password):
        return "admin"
    if secrets.compare_digest(password, settings.user_password):
        return "user"
    raise InvalidCredentialsError("密码不正确")


def create_access_token(role: Literal["user", "admin"]) -> tuple[str, int]:
    """生成 JWT。

    返回 (token, expires_in_seconds)。
    """
    settings = get_settings()
    expire_seconds = settings.jwt_expire_minutes * 60
    now = datetime.now(timezone.utc)
    payload = {
        "sub": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expire_seconds)).timestamp()),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, expire_seconds


def decode_token(token: str) -> TokenPayload:
    """解码 JWT，校验签名 + 过期。

    失败抛 jwt.PyJWTError。
    """
    settings = get_settings()
    data = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    return TokenPayload(
        sub=data["sub"],
        iat=int(data["iat"]),
        exp=int(data["exp"]),
    )
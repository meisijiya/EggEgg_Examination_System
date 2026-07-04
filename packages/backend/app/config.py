"""应用配置层 — 通过 pydantic-settings 读环境变量。

所有配置项均有默认值或为可选，便于本地测试。
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用全局配置。

    字段从 .env / 环境变量自动读取，所有字段均提供默认值以便测试。
    """

    # JWT 配置
    jwt_secret: str = Field(default="dev-secret-change-me", alias="JWT_SECRET")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    jwt_expire_minutes: int = Field(default=43200, alias="JWT_EXPIRE_MINUTES")

    # 用户密码（区分 USER / ADMIN）
    user_password: str = Field(default="dev-user", alias="USER_PASSWORD")
    admin_password: str = Field(default="dev-admin", alias="ADMIN_PASSWORD")

    # 数据库
    database_url: str = Field(
        default="sqlite+aiosqlite:///./data/finance.db", alias="DATABASE_URL"
    )
    app_db_url: str = Field(
        default="sqlite+aiosqlite:///./data/app.db", alias="APP_DB_URL"
    )

    # 判分阈值
    min_coverage: float = Field(default=0.6, alias="MIN_COVERAGE")

    # CORS
    cors_origins: str = Field(
        default="http://localhost:5173,http://localhost:3000", alias="CORS_ORIGINS"
    )

    # 应用元信息
    app_name: str = Field(default="Finance Exam System", alias="APP_NAME")
    debug: bool = Field(default=False, alias="DEBUG")

    # DeepSeek / OpenAI 兼容 LLM（仅 AI 讲解模块使用）
    # ponytail: key 未配置时整个 client 进入 fallback 模式，spec 允许 graceful degrade
    deepseek_api_key: str | None = Field(default=None, alias="DEEPSEEK_API_KEY")
    deepseek_base_url: str = Field(
        default="https://api.deepseek.com/v1", alias="DEEPSEEK_BASE_URL"
    )
    deepseek_model: str = Field(default="deepseek-chat", alias="DEEPSEEK_MODEL")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def cors_origin_list(self) -> list[str]:
        """解析 CORS_ORIGINS 为列表。"""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def project_root(self) -> Path:
        """返回项目根路径（packages/backend 的父级）。"""
        return Path(__file__).resolve().parent.parent.parent


@lru_cache
def get_settings() -> Settings:
    """获取配置单例 — lru_cache 保证全局唯一实例。"""
    return Settings()
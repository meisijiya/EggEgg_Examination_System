"""数据库连接层（SQLAlchemy 2 async）。

两个数据库：
- 题库（data/final/finance.db, 只读）：subjects/chapters/questions
- 应用库（data/app.db, 读写）：exam_attempts / attempt_answers
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""

    pass


# --- 题库引擎（只读） ---
_question_engine: AsyncEngine | None = None
_question_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """获取题库 SQLite 异步引擎（只读访问 questions/chapters/subjects）。"""
    global _question_engine
    if _question_engine is None:
        settings = get_settings()
        # 确保父目录存在
        db_path = _extract_sqlite_path(settings.database_url)
        if db_path and not db_path.exists():
            raise FileNotFoundError(f"题库数据库未找到: {db_path}")
        _question_engine = create_async_engine(
            settings.database_url,
            echo=False,
            future=True,
        )
    return _question_engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """获取题库的异步 session 工厂。"""
    global _question_factory
    if _question_factory is None:
        _question_factory = async_sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            class_=AsyncSession,
        )
    return _question_factory


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖注入：题库 session。"""
    factory = get_session_factory()
    async with factory() as session:
        yield session


# --- 应用库引擎（读写） ---
_app_engine: AsyncEngine | None = None
_app_factory: async_sessionmaker[AsyncSession] | None = None


def get_app_engine() -> AsyncEngine:
    """获取应用数据库异步引擎（写 exam_attempts/attempt_answers）。"""
    global _app_engine
    if _app_engine is None:
        settings = get_settings()
        db_path = _extract_sqlite_path(settings.app_db_url)
        if db_path:
            db_path.parent.mkdir(parents=True, exist_ok=True)
        _app_engine = create_async_engine(
            settings.app_db_url,
            echo=False,
            future=True,
        )
    return _app_engine


def get_app_session_factory() -> async_sessionmaker[AsyncSession]:
    """获取应用库异步 session 工厂。"""
    global _app_factory
    if _app_factory is None:
        _app_factory = async_sessionmaker(
            bind=get_app_engine(),
            expire_on_commit=False,
            class_=AsyncSession,
        )
    return _app_factory


async def get_app_db() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖注入：应用库 session。"""
    factory = get_app_session_factory()
    async with factory() as session:
        yield session


def _extract_sqlite_path(url: str) -> Path | None:
    """从 sqlite+aiosqlite:///PATH 中提取本地路径。"""
    if "sqlite" not in url:
        return None
    # 形如 sqlite+aiosqlite:///abs/path  或  sqlite+aiosqlite:///./rel/path
    prefix = "sqlite+aiosqlite:///"
    if url.startswith(prefix):
        p = url[len(prefix):]
        if p.startswith("/"):
            return Path(p)
        # 相对路径（./foo.db）
        return Path.cwd() / p
    return None
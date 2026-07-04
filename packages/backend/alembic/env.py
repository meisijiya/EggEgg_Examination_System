"""Alembic 异步迁移环境配置。

使用 application 的 settings.app_db_url + Base.metadata 作为迁移源。
"""
import asyncio
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# 把项目根加入 sys.path，便于 alembic 找到 app.* 模块
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 从应用配置同步 URL — 这样 alembic 共享 .env 而不是 alembic.ini
from app.config import get_settings  # noqa: E402

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.app_db_url)

# 引入 Base + 所有 model 以便 autogenerate
from app.models.database import Base  # noqa: E402
from app.models.attempt import AttemptAnswer, ExamAttempt  # noqa: E402, F401

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


# 仅迁移属于应用库的表（exam_attempts / attempt_answers）
# 题库三表（subjects/chapters/questions）由 preprocessor 写入，只读
APP_ONLY_TABLES = {"exam_attempts", "attempt_answers"}


def include_object(object, name, type_, reflected, compare_to):
    """仅 include 应用库表，其余跳过。"""
    if type_ == "table" and name not in APP_ONLY_TABLES:
        return False
    return True


def do_run_migrations(connection: Connection) -> None:
    # 应用库与题库分离，FK 在应用库侧不强制（避免跨库引用失败）
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """异步迁移入口。"""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
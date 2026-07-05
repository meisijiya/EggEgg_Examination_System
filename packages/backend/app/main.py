"""FastAPI 应用入口。

路由：
- POST /auth/login
- POST /exams/start, GET /exams/{id}, POST /exams/{id}/submit, GET /exams/{id}/result
- DELETE /exams/{id}  （fix-22 新增）
- POST /exams/{id}/explain
- GET /dashboard
- GET /admin/review/queue, POST /admin/review/questions/{id}
- GET /health
- GET /               — SPA 入口（开发期由 packages/frontend/dist 服务）
- GET /assets/*       — 静态资源托管（开发期同上）
- 兜底 middleware   — 任何非 API 路径 404 → 返回 index.html（SPA 路由）

启动后自动：
1. 校验题库 finance.db 可读 + 题目数 ≥ 100
2. 确保 app.db 已迁移（运行 alembic upgrade head 兜底）
3. 强制 TZ=Asia/Shanghai（fix-22 — 影响 logging 时间戳 / time.localtime() 等）
"""
from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

# 强制时区为上海 — 必须在 logging / datetime 任何使用之前。
# 注意：`datetime.utcnow()` 仍返回 UTC（与 TZ 无关）；这里影响 C 库
# localtime() 行为（logging、time.strftime 等）。
os.environ.setdefault("TZ", "Asia/Shanghai")
try:
    time.tzset()  # Linux/macOS 有效；Windows 抛 InvalidTZif 但已被 except 兜底
except (AttributeError, OSError):
    pass

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select, text
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.api import admin as admin_api
from app.api import auth as auth_api
from app.api import dashboard as dashboard_api
from app.api import exams as exams_api
from app.api import explain as explain_api
from app.api import subjects as subjects_api
from app.config import get_settings
from app.models.database import get_session_factory
from app.models.question import Question
from app.schemas import HealthResponse

logger = logging.getLogger("fes.backend")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def _resolve_static_dir() -> Path | None:
    """解析前端 dist 路径（容器期优先, 开发期 fallback）。

    - 容器期：dist 由多阶段构建 COPY 到 /app/static（固定路径）
    - 开发期：CWD 跑 uvicorn 时，按 `__file__` 反推 repo 根 + packages/frontend/dist
    - 都不存在 → 返回 None（caller 完全跳过静态服务）

    Phase 5 fix: 原 eager-evaluated candidates list 在容器内 parents[3] 不存在,
    Python 构造 list 时直接抛 IndexError, fallback /app/static 永远轮不到.
    改为容器期优先 + 显式 exists() check, 开发期 fallback 用 try/except.
    """
    # 容器期：/app/static 是多阶段构建 COPY 的固定路径, 优先检查避免触发 IndexError
    container_dist = Path("/app/static")
    if (container_dist / "index.html").exists():
        return container_dist
    # 开发期：{repo}/packages/frontend/dist（main.py 在 packages/backend/app/main.py）
    # 容器内 parents 只有 3 级 (/app/app → /app → /), parents[3] 抛 IndexError → 用 try/except 兜底
    try:
        dev_dist = Path(__file__).resolve().parents[3] / "packages" / "frontend" / "dist"
        if (dev_dist / "index.html").exists():
            return dev_dist
    except IndexError:
        pass
    return None


# 全局常量：解析一次，测试也读得到。
STATIC_DIR = _resolve_static_dir()
STATIC_AVAILABLE = STATIC_DIR is not None


# API 前缀白名单 — SPA fallback middleware 用以区分 "API 404" 和 "前端路由 404"
# ponytail: `/admin` / `/dashboard` 不在白名单，与 SPA 路径冲突的归属问题靠
#   `response.status_code != 404` 这一断言解决：API 真路径返回 401/403/200，
#   middleware 不接管；前端 SPA 路径返回 404，由 middleware 接管。
_API_PREFIXES: tuple[str, ...] = (
    "/api",
    "/auth",
    "/exams",
    "/admin/review",
    "/health",
    "/assets",
    "/docs",
    "/openapi.json",
    "/redoc",
)


async def _validate_question_db() -> tuple[bool, int | None]:
    """校验题库 SQLite 可读 + 题目数。

    返回 (ok, count)。count=None 表示失败。
    """
    try:
        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(select(func.count(Question.id)))
            count = int(result.scalar() or 0)
            ok = count >= 100  # 业务期望至少 100 道题
            return ok, count
    except Exception as e:
        logger.error("题库校验失败: %s", e)
        return False, None


def _ensure_app_db_migrated() -> None:
    """兜底：若 app.db 缺表则同步触发 alembic upgrade。

    部署时通常通过 entrypoint 显式跑 alembic；此处防御性兜底。
    """
    settings = get_settings()
    # 解析 SQLite 路径
    url = settings.app_db_url
    if "sqlite" not in url:
        return
    db_path_str = url.split("///")[-1]
    if db_path_str.startswith("/"):
        db_path = Path(db_path_str)
    else:
        db_path = Path.cwd() / db_path_str.lstrip("./")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # 简单检查：表是否存在
    import sqlite3

    try:
        with sqlite3.connect(db_path) as conn:
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='exam_attempts'"
            )
            if cur.fetchone() is None:
                logger.warning("app.db 缺表，运行 alembic upgrade head")
                import subprocess

                subprocess.run(
                    ["alembic", "upgrade", "head"],
                    cwd=Path(__file__).resolve().parent.parent,
                    check=True,
                )
    except Exception as e:
        logger.warning("app.db 迁移兜底检查失败: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动校验 + 关闭清理。"""
    settings = get_settings()
    logger.info("启动 %s (debug=%s)", settings.app_name, settings.debug)

    # 1. 题库校验
    ok, count = await _validate_question_db()
    if ok:
        logger.info("题库校验通过：%d 道题", count)
    else:
        logger.error("题库校验失败（count=%s）", count)

    # 2. 应用库迁移兜底
    _ensure_app_db_migrated()

    yield
    # 关闭：清理引擎
    from app.models.database import get_app_engine, get_engine

    try:
        await get_engine().dispose()
    except Exception:
        pass
    try:
        await get_app_engine().dispose()
    except Exception:
        pass


class SPAFallbackMiddleware(BaseHTTPMiddleware):
    """SPA 路由回退：非 API 路径返回 404 时，serve index.html。

    FastAPI 不允许在 mount 后注册 catch-all 路由，因此用 middleware 实现。
    仅对未命中 API 前缀白名单的路径生效，避免错误拦截 API 错误响应。

    注意：`/admin` / `/dashboard` 与 API 共名（如 `/admin/review/queue`）——靠
    `response.status_code == 404` 这一断言做"白名单"：API 真路径是 401/403/200，
    middleware 不会误判；而这些路径单独存在时（如浏览器直接访问 `/admin`），
    FastAPI 返回 404，由 middleware 接管回退到 index.html。
    """

    async def dispatch(self, request: Request, call_next):
        if request.method != "GET":
            return await call_next(request)
        response = await call_next(request)
        if response.status_code != 404:
            return response
        if not STATIC_AVAILABLE or STATIC_DIR is None:
            return response
        path = request.url.path
        if any(path.startswith(p) for p in _API_PREFIXES):
            return response
        # 静态文件路径（.css/.js/.png 等）命中真缺失，**不要** fallback
        # （避免 SPA 路由加载时把丢图当 SPA — 那是真的 404）
        last_segment = path.rsplit("/", 1)[-1]
        if "." in last_segment:
            return response
        index = STATIC_DIR / "index.html"
        if not index.exists():
            return response
        return FileResponse(index, status_code=200, media_type="text/html")


def create_app() -> FastAPI:
    """应用工厂。"""
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="财务管理考试系统 — Backend MVP (Phase 1+2+3+5 整合)",
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # SPA fallback middleware（必须在路由 / mount 前注册）
    if STATIC_AVAILABLE:
        app.add_middleware(SPAFallbackMiddleware)

    # 静态资源挂载（/assets → dist/assets）
    if STATIC_AVAILABLE and STATIC_DIR is not None:
        assets_dir = STATIC_DIR / "assets"
        if assets_dir.exists():
            app.mount(
                "/assets",
                StaticFiles(directory=str(assets_dir)),
                name="assets",
            )
            logger.info("已挂载 /assets → %s", assets_dir)

    # 路由
    app.include_router(auth_api.router)
    app.include_router(subjects_api.router)
    app.include_router(exams_api.router)
    app.include_router(explain_api.router)
    app.include_router(dashboard_api.router)
    app.include_router(admin_api.router)

    @app.get("/health", response_model=HealthResponse, tags=["health"])
    async def health() -> HealthResponse:
        """健康检查端点。"""
        ok, count = await _validate_question_db()
        return HealthResponse(
            status="ok" if ok else "degraded",
            database=ok,
            question_count=count,
            app_name=settings.app_name,
        )

    # SPA 入口
    if STATIC_AVAILABLE and STATIC_DIR is not None:

        @app.get("/", response_class=FileResponse, include_in_schema=False)
        async def serve_index():
            """SPA 入口：返回 index.html。"""
            index = STATIC_DIR / "index.html"
            if index.exists():
                return FileResponse(index)
            # 兜底：build 之后被删了 dist
            return {"message": "Frontend build missing"}

    else:

        @app.get("/", tags=["root"])
        async def root_no_frontend():
            """未 build dist 时返回 JSON 提示。"""
            return {
                "message": (
                    "Frontend not built. "
                    "Run `cd packages/frontend && pnpm build` first."
                ),
                "docs": "/docs",
                "health": "/health",
            }

    return app


app = create_app()
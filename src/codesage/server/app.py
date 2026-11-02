"""FastAPI 应用装配：路由 → 静态资源 → SPA 回落。

挂载顺序不能变：API 路由必须在 catch-all 之前，否则 `/api/*` 会被 index.html 吞掉。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

import codesage
from codesage.core.config import Settings, load_settings
from codesage.server.errors import ApiError, install_error_handlers
from codesage.server.jobs import JobManager
from codesage.server.routers import evals, feedback, jobs, lessons, reports, system, workspace
from codesage.server.security import LocalGuardMiddleware
from codesage.services.workspace_service import WorkspaceRegistry

STATIC_DIR = Path(__file__).resolve().parent / "static"

_BUILD_HINT = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>CodeSage GUI 尚未构建</title>
<style>body{font-family:system-ui;max-width:640px;margin:80px auto;line-height:1.8;color:#1f2937}
code{background:#f3f4f6;padding:2px 6px;border-radius:4px}</style></head>
<body>
<h2>前端产物不存在</h2>
<p>后端 API 已就绪，但 <code>src/codesage/server/static/index.html</code> 还没生成。</p>
<p>在项目根目录执行：</p>
<pre><code>python scripts/build_ui.py</code></pre>
<p>或者开发态双进程：</p>
<pre><code>cd web &amp;&amp; npm ci &amp;&amp; npm run dev
codesage ui --dev</code></pre>
<p>API 文档：<a href="/api/docs">/api/docs</a></p>
</body></html>"""


def create_app(
    settings: Settings | None = None,
    registry: WorkspaceRegistry | None = None,
    *,
    enable_spa: bool = True,
) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        app.state.jobs.shutdown()

    app = FastAPI(
        title="CodeSage GUI API",
        version=codesage.__version__,
        description="本地代码审查 Agent 的浏览器接口（仅回环地址）",
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    app.state.settings = settings
    app.state.registry = registry or WorkspaceRegistry()
    app.state.jobs = JobManager(settings)

    app.add_middleware(LocalGuardMiddleware)
    install_error_handlers(app)

    api = APIRouter(prefix="/api")
    api.include_router(system.router)
    api.include_router(workspace.router)
    api.include_router(jobs.router)
    api.include_router(reports.router)
    api.include_router(feedback.router)
    api.include_router(lessons.router)
    api.include_router(evals.router)
    app.include_router(api)

    if enable_spa:
        _mount_spa(app)

    return app


def _mount_spa(app: FastAPI) -> None:
    assets = STATIC_DIR / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str):
        if full_path.startswith("api/"):
            raise ApiError(404, "not_found", f"接口不存在：/{full_path}")
        index = STATIC_DIR / "index.html"
        if not index.is_file():
            return HTMLResponse(_BUILD_HINT, status_code=503)
        return FileResponse(
            str(index),
            media_type="text/html",
            headers={"Cache-Control": "no-cache"},
        )

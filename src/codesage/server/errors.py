"""统一错误体与异常映射。

前端只需要认 {code, message, detail} 三个字段，不必解析 FastAPI 默认的 detail 字符串。
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from codesage.memory.store import StoreBusyError
from codesage.server.jobs import JobBusy
from codesage.services.workspace_service import (
    PathForbidden,
    PathOutsideRoot,
    PathUnsupported,
    WorkspaceError,
)


class ApiError(Exception):
    """业务层主动抛出的 HTTP 错误。"""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        detail: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.detail = detail


def _body(code: str, message: str, detail: Any = None) -> dict:
    return {"code": code, "message": message, "detail": detail or {}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_body(exc.code, exc.message, exc.detail),
        )

    @app.exception_handler(PathOutsideRoot)
    async def _outside(_: Request, exc: PathOutsideRoot) -> JSONResponse:
        return JSONResponse(status_code=403, content=_body(exc.code, str(exc)))

    @app.exception_handler(PathForbidden)
    async def _forbidden(_: Request, exc: PathForbidden) -> JSONResponse:
        return JSONResponse(status_code=403, content=_body(exc.code, str(exc)))

    @app.exception_handler(PathUnsupported)
    async def _unsupported(_: Request, exc: PathUnsupported) -> JSONResponse:
        return JSONResponse(status_code=415, content=_body(exc.code, str(exc)))

    @app.exception_handler(WorkspaceError)
    async def _workspace(_: Request, exc: WorkspaceError) -> JSONResponse:
        status = 404 if exc.code == "invalid_path" else 400
        return JSONResponse(status_code=status, content=_body(exc.code, str(exc)))

    @app.exception_handler(JobBusy)
    async def _busy(_: Request, exc: JobBusy) -> JSONResponse:
        return JSONResponse(
            status_code=429,
            content=_body(
                "busy",
                str(exc),
                {"retry_after": exc.retry_after},
            ),
            headers={"Retry-After": str(exc.retry_after)},
        )

    @app.exception_handler(StoreBusyError)
    async def _store_busy(_: Request, exc: StoreBusyError) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content=_body("store_busy", "存储繁忙，请稍后重试", {"error": str(exc)}),
        )

    @app.exception_handler(FileNotFoundError)
    async def _not_found(_: Request, exc: FileNotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=404, content=_body("not_found", str(exc) or "资源不存在")
        )

    @app.exception_handler(ValueError)
    async def _value_error(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content=_body("invalid_request", str(exc)))

"""本地服务的安全边界。

虽然只监听 127.0.0.1、无登录，仍要防两件事：
1. DNS rebinding：恶意网页把自己的域名解析到 127.0.0.1 后调用本地 API
   → 校验 Host 必须是回环名；
2. 跨站写操作：浏览器表单/脚本跨源 POST 到本地服务
   → 校验 Origin（有 Origin 时必须在白名单内；非浏览器请求没有 Origin，放行）。
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def is_loopback(host: str) -> bool:
    host = (host or "").strip()
    if host in LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def ensure_loopback_host(host: str) -> str:
    """启动前校验绑定地址（拒绝 0.0.0.0）。"""
    if not is_loopback(host):
        raise ValueError(
            f"出于安全考虑，GUI 只允许绑定回环地址（127.0.0.1 / localhost），收到：{host}"
        )
    return host


def _host_only(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if value.startswith("["):  # [::1]:8787
        return value.split("]")[0] + "]"
    return value.rsplit(":", 1)[0] if ":" in value else value


class LocalGuardMiddleware(BaseHTTPMiddleware):
    """Host / Origin 校验（仅本机可访问）。"""

    async def dispatch(self, request: Request, call_next) -> Response:
        host = _host_only(request.headers.get("host", ""))
        if host and not is_loopback(host):
            return JSONResponse(
                status_code=403,
                content={
                    "code": "host_not_allowed",
                    "message": f"只允许通过回环地址访问（收到 Host: {host}）",
                    "detail": {},
                },
            )

        if request.method not in SAFE_METHODS:
            origin = request.headers.get("origin", "")
            if origin:
                parsed = urlparse(origin)
                if not is_loopback(parsed.hostname or ""):
                    return JSONResponse(
                        status_code=403,
                        content={
                            "code": "origin_not_allowed",
                            "message": f"跨源写请求被拒绝（Origin: {origin}）",
                            "detail": {},
                        },
                    )

        return await call_next(request)

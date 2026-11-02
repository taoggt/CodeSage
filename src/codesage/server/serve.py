"""启动本地服务（uvicorn 单进程单 worker）。"""

from __future__ import annotations

import threading
import webbrowser
from pathlib import Path

from codesage.core.config import load_settings
from codesage.server.app import create_app
from codesage.server.security import ensure_loopback_host
from codesage.services.workspace_service import WorkspaceRegistry


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 8787,
    open_browser: bool = True,
    path: str | Path | None = None,
    dev: bool = False,
    config_file: str | Path | None = None,
) -> None:
    """启动 GUI。

    单进程是硬约束：Job / 事件缓冲 / 取消令牌 / 线程池都在进程内。
    多 worker 会让「创建任务」与「订阅事件」落到不同进程，直接 404。
    """
    host = ensure_loopback_host(host)
    settings = load_settings(config_file)
    settings.server.host = host
    settings.server.port = port

    registry = WorkspaceRegistry()
    if path is not None:
        registry.register(path)

    app = create_app(settings, registry, enable_spa=not dev)
    url = f"http://{host}:{port}"

    print(f"CodeSage GUI → {url}（single-process mode）")
    print(f"  存储：{settings.review.store_path or '~/.codesage/store.db'}")
    if dev:
        print("  开发模式：请另开终端 cd web && npm run dev（Vite 5173 代理 /api）")
    elif not (settings and (Path(__file__).parent / "static" / "index.html").is_file()):
        print("  提示：前端未构建，页面会显示构建指引；API 仍可用（/api/docs）")

    if open_browser and not dev:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    import uvicorn

    try:
        uvicorn.run(
            app,
            host=host,
            port=port,
            workers=1,
            log_level="info" if dev else "warning",
            access_log=dev,
        )
    except OSError as exc:  # 端口占用等
        raise SystemExit(f"启动失败：{exc}（换端口：codesage ui --port 8790）") from exc

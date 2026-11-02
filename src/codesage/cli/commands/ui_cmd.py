"""ui 命令：启动浏览器 GUI（本地回环地址）。

web 依赖是可选安装（pip install -e 加上 web 选项，见 docs/GUI.md）。
本模块顶层只 import typer，FastAPI/uvicorn 在函数体内懒加载——
保证不装 web extra 时其余 CLI 命令完全不受影响。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

console = Console()


def ui(
    path: Optional[Path] = typer.Option(
        None, "--path", "-p", help="启动时打开的工作区目录（默认当前目录）"
    ),
    port: int = typer.Option(8787, "--port", help="监听端口"),
    host: str = typer.Option(
        "127.0.0.1", "--host", help="监听地址（只允许回环地址）"
    ),
    open_browser: bool = typer.Option(
        True, "--browser/--no-browser", help="启动后自动打开浏览器"
    ),
    dev: bool = typer.Option(
        False, "--dev", help="开发模式：不挂载前端产物（配合 Vite dev server）"
    ),
    config: Optional[Path] = typer.Option(
        None, "--config", help="指定 codesage.toml（默认按当前目录探测）"
    ),
) -> None:
    """启动本地浏览器 GUI（审查 / 历史 / 错题本 / 评测）。

    示例:
      codesage ui                          # 打开当前目录
      codesage ui --path D:/proj/src       # 指定工作区
      codesage ui --port 8790 --no-browser # 换端口、不自动开浏览器
      codesage ui --dev                    # 前端开发模式（Vite HMR）
    """
    try:
        from codesage.server.serve import serve
    except ImportError as exc:  # 缺 fastapi / uvicorn
        console.print(
            "[red]缺少 web 依赖：[/red]"
            f"{exc}\n"
            "[dim]安装：pip install -e . 并附带 web 可选依赖（详见 docs/GUI.md）[/dim]"
        )
        raise typer.Exit(2) from exc

    serve(
        host=host,
        port=port,
        open_browser=open_browser,
        path=path or Path.cwd(),
        dev=dev,
        config_file=config,
    )

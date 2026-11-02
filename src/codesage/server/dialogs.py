"""本机原生目录选择对话框。

为什么必须放在服务端：
浏览器出于安全限制，无法获得用户选中目录的**绝对路径**——
`showDirectoryPicker()` 只给句柄、拖拽只给 entry、`webkitdirectory` 只给相对路径。
而这些都不足以让后端去读同一台机器上的源码，所以只能由本地进程弹系统对话框。

实现与限制：
- Windows / Linux 用 tkinter 的 askdirectory；调用发生在 FastAPI 的线程池线程里
  （路由声明为 def），不会阻塞事件循环；
- macOS 的 Tk 必须在主线程创建，而主线程被 uvicorn 事件循环占用 →
  直接抛 DialogUnavailable，由前端回退到"目录浏览"；
- 全局串行：同时只允许一个对话框，避免用户连点弹出多个模态。
"""

from __future__ import annotations

import platform
import threading
from pathlib import Path


class DialogUnavailable(RuntimeError):
    """当前环境无法弹出系统对话框。"""


_LOCK = threading.Lock()


def available() -> bool:
    """tkinter 是否可用（不实际弹窗）。"""
    try:
        import tkinter  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def platform_supported() -> bool:
    """macOS 上 Tk 需要主线程，当前架构下不可用。"""
    return available() and platform.system() != "Darwin"


def ask_directory(initial: str | None = None, title: str = "选择 CodeSage 工作区") -> str | None:
    """弹出系统目录选择框，返回绝对路径；用户取消返回 None。"""
    if not available():
        raise DialogUnavailable("未安装 tkinter，无法弹出系统对话框")
    if platform.system() == "Darwin":
        raise DialogUnavailable("macOS 下 Tk 需要主线程，请使用目录浏览")

    import tkinter as tk
    from tkinter import filedialog

    start = str(Path(initial).expanduser()) if initial else str(Path.home())

    with _LOCK:
        try:
            root = tk.Tk()
        except Exception as exc:  # noqa: BLE001 - 无图形环境（如纯命令行 Linux）
            raise DialogUnavailable(f"无法初始化图形界面：{exc}") from exc

        root.withdraw()
        try:
            root.attributes("-topmost", True)
            root.update()
        except Exception:  # noqa: BLE001 - 个别窗口管理器不支持 topmost
            pass

        try:
            selected = filedialog.askdirectory(
                parent=root,
                initialdir=start,
                title=title,
                mustexist=True,
            )
        finally:
            try:
                root.destroy()
            except Exception:  # noqa: BLE001
                pass

    chosen = str(selected).strip()
    return chosen or None

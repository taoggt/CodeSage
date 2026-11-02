"""存储连接工厂。

约定：
- 每个请求 / 每个后台任务各自 open 一个 Store，**不共享实例**
  （sqlite 连接不是线程安全的，共享会踩 ProgrammingError；WAL 下建连成本可忽略）；
- 统一在这里解析 store_path，避免 CLI 写到 A 库、GUI 读到 B 库的老问题。
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from codesage.core.config import Settings
from codesage.memory.store import Store, default_store_path


def resolve_store_path(settings: Settings) -> Path:
    """配置里的 store_path 优先；空则用用户级默认路径。"""
    configured = (settings.review.store_path or "").strip()
    return Path(configured).expanduser() if configured else default_store_path()


@contextmanager
def open_store(settings: Settings) -> Iterator[Store]:
    """按配置打开存储，退出时关闭。"""
    store = Store(resolve_store_path(settings))
    try:
        yield store
    finally:
        store.close()


def store_available(settings: Settings) -> bool:
    """探测存储是否可写（供 /api/health 与 doctor 页使用）。"""
    try:
        with open_store(settings) as store:
            store.review_count()
        return True
    except Exception:  # noqa: BLE001
        return False

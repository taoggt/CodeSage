"""业务服务层：CLI 与浏览器 GUI 共用的逻辑在这里。

分层纪律：
- services 不 import FastAPI（不感知 HTTP）；
- routers 不直接操作 sqlite / pipeline（只做协议转换与校验）。
"""

from __future__ import annotations

__all__ = [
    "eval_service",
    "export_service",
    "feedback_service",
    "history_service",
    "lesson_service",
    "review_service",
    "store_factory",
    "workspace_service",
]

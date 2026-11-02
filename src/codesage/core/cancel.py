"""协作式取消信号。

设计要点：
- 零依赖（只用标准库），CLI 默认不传（None），行为与改动前完全一致；
- 内核只做"检查点"，不负责打断正在进行的同步 HTTP 调用
  —— 一次 LLM 请求最长 timeout 秒，取消会在该请求返回后生效；
- 用 threading.Event 而非 asyncio，因为审查跑在线程池里。
"""

from __future__ import annotations

import threading


class ReviewCancelled(Exception):
    """显式取消（目前仅由调用方在需要硬中断时使用）。"""


class CancelToken:
    """可在任意线程置位、在任意线程检查的取消令牌。"""

    __slots__ = ("_event",)

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def reset(self) -> None:
        self._event.clear()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        """需要"立即中断"语义时调用（内核主链路目前不用它）。"""
        if self._event.is_set():
            raise ReviewCancelled("任务已取消")

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return f"CancelToken(cancelled={self.cancelled})"

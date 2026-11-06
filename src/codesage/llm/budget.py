"""Token 预算与熔断。

三层阈值（与三区记忆模型配套），比例的分母是 **工作预算 budget_window**，
不是模型标称窗口 context_window——后者可能有 1M，但一次代码审查用不到，
拿它当分母只会让压缩与熔断永远不触发。

    ratio < soft   → 正常执行
    ratio ≥ soft   → SOFT：回合边界触发压缩（不打断当前推理）
    ratio ≥ hard   → HARD：调用前强制压缩（阻塞，保证下一轮能放下）
    ratio ≥ abort  → ABORT：熔断，结束循环并输出已有发现（止损）

为什么需要熔断：宁可输出“不完整但不报错”的结果，也不让 API 调用
因超限失败导致整个审查白跑。
"""

from __future__ import annotations

from enum import Enum

from codesage.core.config import BudgetSettings
from codesage.core.models import TokenUsage


class BudgetState(str, Enum):
    OK = "ok"
    SOFT = "soft"
    HARD = "hard"
    ABORT = "abort"


class TokenBudget:
    """跟踪累计用量 + 计算当前上下文使用率与状态。"""

    def __init__(self, settings: BudgetSettings) -> None:
        self._settings = settings
        self.usage = TokenUsage()

    @property
    def context_window(self) -> int:
        """模型标称上下文上限（只用于展示与超发校验）。"""
        return self._settings.context_window

    @property
    def budget_window(self) -> int:
        """工作预算：三区阈值的分母，不会超过标称窗口。"""
        return self._settings.effective_budget_window

    @property
    def abort_ratio(self) -> float:
        return self._settings.abort_ratio

    def record(self, usage: TokenUsage) -> None:
        """记录一次真实调用用量（以 API 返回为准）。"""
        self.usage.add(usage)

    def ratio(self, current_context_tokens: int) -> float:
        """当前上下文占工作预算的比例（用于触发压缩/熔断）。"""
        if self.budget_window <= 0:
            return 0.0
        return current_context_tokens / self.budget_window

    def state(self, current_context_tokens: int) -> BudgetState:
        ratio = self.ratio(current_context_tokens)
        if ratio >= self._settings.abort_ratio:
            return BudgetState.ABORT
        if ratio >= self._settings.hard_ratio:
            return BudgetState.HARD
        if ratio >= self._settings.soft_ratio:
            return BudgetState.SOFT
        return BudgetState.OK

    def cost_yuan(self) -> float:
        """按配置单价估算累计成本（元）。"""
        return self.usage.cost_yuan(
            self._settings.input_price_per_m,
            self._settings.output_price_per_m,
        )

    def snapshot(self) -> dict[str, float | int]:
        return {
            "calls": self.usage.calls,
            "input_tokens": self.usage.input_tokens,
            "output_tokens": self.usage.output_tokens,
            "total_tokens": self.usage.total,
            "cost_yuan": round(self.cost_yuan(), 4),
        }

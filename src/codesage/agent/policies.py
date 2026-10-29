"""停止条件策略：Agent 循环的安全阀。

四类停止条件（对应规划文档）：
1. 模型主动结束（无 tool_calls）—— 正常完成；
2. 轮次上限 —— 防止无限循环；
3. Token 预算熔断（在 loop 中检查）；
4. 连续 N 轮无新增发现 —— 防止"原地打转"式低效循环。
"""

from __future__ import annotations

from dataclasses import dataclass

from codesage.agent.state import AgentState


@dataclass
class StopPolicy:
    """轮次与进展检查。"""

    max_turns: int = 12
    no_progress_patience: int = 2  # 连续 N 轮无新增发现即停止

    def check(self, state: AgentState) -> tuple[bool, str]:
        """返回 (是否停止, 原因)。

        无进展判定的两种情形（教训来自真实案例——LLM 在前 3 轮"调查期"
        读码/跑分析/查知识库被过早掐断）：
        - 已提交过发现后连续 N 轮无新增 → 停滞，停止；
        - 从未提交过任何发现 → 给更宽的宽限期（调查本身是进展，
          模型可能只是尚未到提交时机），宽限用尽才判定完成。
        """
        if state.turn >= self.max_turns:
            return True, f"达到轮次上限（{self.max_turns} 轮）"

        history = state.findings_history
        if not history:
            return False, ""

        ever_found = any(count > 0 for count in history)
        patience = (
            self.no_progress_patience
            if ever_found
            else self.no_progress_patience + 2  # 零提交宽限期
        )
        if state.turn >= 3 and len(history) > patience:
            recent = history[-(patience + 1):]
            if len(set(recent)) == 1:
                if ever_found:
                    return True, f"连续 {patience} 轮无新增发现（当前共 {recent[-1]} 条）"
                return True, f"连续 {patience} 轮未提交任何发现（判定审查完成）"
        return False, ""

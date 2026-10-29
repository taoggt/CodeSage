"""循环状态：供停止策略判断、调试与序列化。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AgentState:
    """ReAct 循环的运行状态。"""

    turn: int = 0
    status: str = "running"  # running / finished / aborted
    stop_reason: str = ""
    tool_calls_total: int = 0
    findings_history: list[int] = field(default_factory=list)  # 每轮末的累计发现数

    def snapshot(self) -> dict:
        return {
            "turn": self.turn,
            "status": self.status,
            "stop_reason": self.stop_reason,
            "tool_calls_total": self.tool_calls_total,
            "findings_history": list(self.findings_history),
        }

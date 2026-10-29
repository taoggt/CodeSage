"""Agent 内核：自研 ReAct 循环（不依赖任何 Agent 框架）。

模块划分：
- registry: 工具注册表（@tool 装饰器 + JSON Schema 自动生成）
- state:    循环状态（供快照与调试）
- policies: 停止条件
- context:  初始上下文组装（任务简报）
- profiles: 角色 Prompt（审查维度、守则）
- loop:     ReAct 主循环（LLM ↔ 工具 交替执行）
"""

from codesage.agent.loop import AgentLoop, AgentResult, LoopConfig
from codesage.agent.profiles import PROFILES, Profile, build_system_prompt
from codesage.agent.registry import Tool, ToolRegistry, tool
from codesage.agent.state import AgentState

__all__ = [
    "AgentLoop",
    "AgentResult",
    "LoopConfig",
    "AgentState",
    "Tool",
    "ToolRegistry",
    "tool",
    "Profile",
    "PROFILES",
    "build_system_prompt",
]

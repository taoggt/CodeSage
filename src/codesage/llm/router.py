"""模型分级路由：把贵的 token 用在真正需要推理的地方。

任务分级：
- DECISION / COMPRESS：工具决策与摘要压缩 → 快模型（便宜、够用）
- REVIEW：深度审查与最终总结 → 强模型（质量优先）
- JUDGE：评测环节的 LLM-as-judge → 强模型
"""

from __future__ import annotations

from enum import Enum

from codesage.core.config import LLMSettings


class TaskKind(str, Enum):
    """LLM 调用任务类型。"""

    DECISION = "decision"  # ReAct 循环内的工具决策
    COMPRESS = "compress"  # 记忆压缩摘要
    REVIEW = "review"      # 深度审查 / 最终总结
    JUDGE = "judge"        # 评测打分（LLM-as-judge）


class ModelRouter:
    """按任务类型选择模型。"""

    def __init__(self, settings: LLMSettings) -> None:
        self._settings = settings

    def model_for(self, kind: TaskKind) -> str:
        # 压缩摘要属于轻任务；工具决策与深度审查共享同一次推理调用
        # （模型在决策的同时就在分析代码），必须用强模型保证审查质量。
        if kind == TaskKind.COMPRESS:
            return self._settings.model_fast
        return self._settings.model_strong

    def describe(self) -> dict[str, str]:
        """供 CLI 展示当前路由配置。"""
        return {
            "fast": self._settings.model_fast,
            "strong": self._settings.model_strong,
        }

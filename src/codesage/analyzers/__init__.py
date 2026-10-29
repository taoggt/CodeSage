"""确定性分析层（零 Token）：AST 规则引擎 + Ruff / Bandit / radon 集成。

统一协议：Analyzer.run(filename, source) -> list[Finding]
无论来源是什么，产出都是 core.models.Finding，与 Agent 层解耦。
"""

from codesage.analyzers.ast_rules import (
    RULE_IDS,
    RuleEngine,
    default_rules,
)
from codesage.analyzers.bandit_runner import BanditAnalyzer
from codesage.analyzers.base import Analyzer
from codesage.analyzers.radon_runner import RadonAnalyzer
from codesage.analyzers.ruff_runner import RuffAnalyzer

__all__ = [
    "Analyzer",
    "BanditAnalyzer",
    "RadonAnalyzer",
    "RuffAnalyzer",
    "RuleEngine",
    "default_rules",
    "RULE_IDS",
]


def default_analyzers() -> list[Analyzer]:
    """默认分析器组合（外部工具不可用时会被自动跳过）。"""
    return [RuffAnalyzer(), BanditAnalyzer(), RadonAnalyzer()]

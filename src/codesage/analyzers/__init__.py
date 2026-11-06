"""确定性分析层（零 Token）：AST 规则引擎 + Ruff / Bandit / radon 集成。

统一协议：Analyzer.run(filename, source) -> list[Finding]
无论来源是什么，产出都是 core.models.Finding，与 Agent 层解耦。

v2 多语言：协议新增 `languages` 维度。`default_analyzers()` 在不传参时
行为与 v1 完全一致（返回三元组），传入语言集合时按能力过滤——
这样历史上"工具缺失就整份跳过"的语义不变，但 Java 文件不会再被
喂给只认 Python 的工具。
"""

from codesage.analyzers.ast_rules import (
    RULE_IDS,
    RuleEngine,
    default_rules,
)
from codesage.analyzers.bandit_runner import BanditAnalyzer
from codesage.analyzers.base import Analyzer
from codesage.analyzers.java_rules import JAVA_RULE_IDS, JavaRuleEngine
from codesage.analyzers.radon_runner import RadonAnalyzer
from codesage.analyzers.ruff_runner import RuffAnalyzer

__all__ = [
    "Analyzer",
    "BanditAnalyzer",
    "JAVA_RULE_IDS",
    "JavaRuleEngine",
    "RadonAnalyzer",
    "RuffAnalyzer",
    "RuleEngine",
    "default_analyzers",
    "default_rule_engines",
    "default_rules",
    "RULE_IDS",
]


def default_analyzers(languages: object = None) -> list[Analyzer]:
    """默认分析器组合（外部工具不可用时会被自动跳过）。

    languages 为 None 时返回全部（兼容 v1 调用点：/api/health、readiness 探测
    需要看到"装了哪些工具"，与本次审查的语言无关）；
    传入语言集合时只返回能处理其中任一语言的分析器。
    """
    analyzers: list[Analyzer] = [RuffAnalyzer(), BanditAnalyzer(), RadonAnalyzer()]
    if languages is None:
        return analyzers
    targets = tuple(languages)  # type: ignore[arg-type]
    return [analyzer for analyzer in analyzers if analyzer.supports_any(targets)]


def default_rule_engines() -> list:
    """自研规则引擎组合（每个引擎只处理自己的语言）。

    顺序有意义：Python 在前，保持既有报告里规则发现的稳定排序。
    返回类型标注为 list（而非 list[RuleEngine]）是因为两个引擎是不同类，
    只共享鸭子类型协议：name / language / languages / is_available / run。
    """
    return [RuleEngine(), JavaRuleEngine()]

"""radon 集成：圈复杂度 / 可维护性指标（性能与质量的客观数据）。

radon 作为 Python 库直接调用（比其他分析器少一次子进程开销），
未安装时优雅跳过。
"""

from __future__ import annotations

from codesage.analyzers.base import Analyzer, is_test_file
from codesage.core.languages import Language
from codesage.core.models import Category, Finding, Severity, Source

# 复杂度阈值（radon 惯例：>10 需要关注，>20 需要重构）
COMPLEXITY_MINOR = 10
COMPLEXITY_MAJOR = 20


class RadonAnalyzer(Analyzer):
    name = "radon"
    display = "radon（复杂度分析）"
    # radon 内部用自建的 Python AST visitor，无法处理其他语言
    languages = (Language.PYTHON,)

    def is_available(self) -> bool:
        try:
            import radon  # noqa: F401
        except ImportError:
            return False
        return True

    def run(self, filename: str, source: str) -> list[Finding]:
        # 测试代码豁免：测试函数因大量断言天然具有较高圈复杂度，
        # 这是测试的合理形态，复杂度指标面向的是生产代码的可维护性。
        if is_test_file(filename):
            return []

        try:
            from radon.complexity import cc_visit
        except ImportError:
            return []

        try:
            blocks = cc_visit(source)
        except Exception:  # noqa: BLE001 - 语法错误等交给其他分析器报告
            return []

        findings: list[Finding] = []
        for block in blocks:
            complexity = int(getattr(block, "complexity", 0))
            if complexity < COMPLEXITY_MINOR:
                continue
            name = getattr(block, "name", "<unknown>")
            classname = getattr(block, "classname", None)
            display_name = f"{classname}.{name}" if classname else name
            lineno = int(getattr(block, "lineno", 1))
            endline = int(getattr(block, "endline", lineno) or lineno)

            if complexity >= COMPLEXITY_MAJOR:
                severity = Severity.MAJOR
                advice = "建议拆分函数（提取子函数 / 用字典分派或策略模式消除长分支）"
            else:
                severity = Severity.MINOR
                advice = "建议降低分支密度（提前 return / 合并条件 / 提取辅助函数）"

            findings.append(
                Finding(
                    file=filename,
                    line_start=lineno,
                    line_end=endline,
                    category=Category.PERFORMANCE,
                    severity=severity,
                    title=f"圈复杂度过高：{display_name}（CC={complexity}）",
                    description=(
                        f"函数 {display_name} 的圈复杂度为 {complexity}，"
                        f"超过阈值 {COMPLEXITY_MINOR}。高复杂度函数的测试成本、"
                        "缺陷概率和维护成本都显著上升。"
                    ),
                    suggestion=advice,
                    confidence=1.0,
                    source=Source.RADON,
                    rule_id="radon-cc",
                )
            )
        return findings

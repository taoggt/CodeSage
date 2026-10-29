"""严重级映射测试：安全风险与代码质量提示的可比性（用户反馈驱动）。

背景：真实使用中 SQL 注入（Bandit 标 MEDIUM）与"缺 docstring"
同为 P2 呈现——本文件锁定"高危规则按业务风险上调"的行为。
"""

from codesage.analyzers.bandit_runner import BanditAnalyzer
from codesage.core.models import Severity


def make_item(test_id: str, severity: str, confidence: str = "MEDIUM") -> dict:
    return {
        "test_id": test_id,
        "test_name": test_id.lower(),
        "issue_severity": severity,
        "issue_confidence": confidence,
        "issue_text": "sample issue",
        "line_number": 3,
    }


def test_sql_injection_elevated_to_major():
    """B608（工具标 MEDIUM）按风险上调为 MAJOR。"""
    finding = BanditAnalyzer._to_finding(make_item("B608", "MEDIUM"), "src/app.py")
    assert finding is not None
    assert finding.severity == Severity.MAJOR


def test_eval_elevated_to_major():
    finding = BanditAnalyzer._to_finding(make_item("B307", "MEDIUM"), "src/app.py")
    assert finding is not None
    assert finding.severity == Severity.MAJOR


def test_pickle_elevated_to_major():
    finding = BanditAnalyzer._to_finding(make_item("B301", "MEDIUM"), "src/app.py")
    assert finding is not None
    assert finding.severity == Severity.MAJOR


def test_generic_low_rule_stays_info():
    """普通 LOW 规则不上调（避免把所有噪音都升级）。"""
    finding = BanditAnalyzer._to_finding(make_item("B404", "LOW"), "src/app.py")
    assert finding is not None
    assert finding.severity == Severity.INFO


def test_high_severity_not_downgraded():
    """已是 HIGH 的规则保持 MAJOR（重映射只升不降）。"""
    finding = BanditAnalyzer._to_finding(make_item("B602", "HIGH"), "src/app.py")
    assert finding is not None
    assert finding.severity == Severity.MAJOR


def test_quality_docstring_stays_minor():
    """对照：缺 docstring 维持 MINOR——质量提示的定级合理，
    问题在安全侧偏低而非质量侧偏高。"""
    from codesage.analyzers.ast_rules import RuleEngine

    findings = RuleEngine().run("src/app.py", "def public_api():\n    return 1\n")
    docstring_findings = [f for f in findings if f.rule_id == "CG006"]
    assert docstring_findings
    assert docstring_findings[0].severity == Severity.MINOR


def test_cli_severity_parsing_accepts_names_and_labels():
    """--min-severity / --fail-on 同时接受名称与 P0-P3 标签（大小写不敏感）。

    用户反馈驱动：报告里展示 P1，用户自然输入 P1 却被拒——
    命令行与报告统一指代体系。
    """
    from codesage.cli.commands.review import _parse_severity

    assert _parse_severity("P1") == Severity.MAJOR
    assert _parse_severity("p1") == Severity.MAJOR
    assert _parse_severity("major") == Severity.MAJOR
    assert _parse_severity("MAJOR") == Severity.MAJOR
    assert _parse_severity(" P0 ") == Severity.CRITICAL
    assert _parse_severity("p3") == Severity.INFO
    assert _parse_severity("info") == Severity.INFO
    assert _parse_severity("P9") is None
    assert _parse_severity("") is None

"""误报控制测试：测试代码感知（dogfooding 反馈的回归保护）。

背景：用 CodeSage 审查自己的 tests/ 目录时，发现 208 条候选里
绝大多数是噪音（CG006 缺 docstring 报在测试函数上、Bandit B101
报 assert 用法），评分被扣到 0 分。本文件锁定修复后的行为：

- 测试文件豁免"缺 docstring"规则；
- 测试文件豁免 Bandit B101（assert）/ B108（临时路径）；
- 但真实安全问题在任何文件中都照常报告（豁免不是放水）。
"""

from codesage.analyzers.ast_rules import RuleEngine
from codesage.analyzers.bandit_runner import BanditAnalyzer
from codesage.analyzers.base import is_test_file
from codesage.analyzers.radon_runner import RadonAnalyzer


def test_is_test_file_recognition():
    assert is_test_file("tests/test_a.py")
    assert is_test_file("test_a.py")
    assert is_test_file("a_test.py")
    assert is_test_file("pkg/tests/helper.py")
    assert is_test_file("subdir/test/helper.py")
    assert is_test_file("conftest.py")
    assert is_test_file("tests\\test_a.py")  # Windows 反斜杠路径
    assert not is_test_file("src/app.py")
    assert not is_test_file("src/contest.py")   # 不误伤相近单词
    assert not is_test_file("latest_main.py")


def test_docstring_rule_exempts_test_files():
    engine = RuleEngine()
    source = "def helper():\n    return 1\n"

    production = engine.run("src/app.py", source)
    assert any(f.rule_id == "CG006" for f in production)

    in_tests = engine.run("tests/test_app.py", source)
    assert not any(f.rule_id == "CG006" for f in in_tests)


def test_bandit_skips_b101_in_test_files():
    item = {
        "test_id": "B101",
        "test_name": "assert_used",
        "issue_severity": "LOW",
        "issue_confidence": "HIGH",
        "issue_text": "Use of assert detected.",
        "line_number": 5,
        "code": "assert result == 1",
    }
    assert BanditAnalyzer._to_finding(item, "tests/test_a.py") is None
    assert BanditAnalyzer._to_finding(item, "src/app.py") is not None


def test_bandit_skips_b108_in_test_files():
    item = {
        "test_id": "B108",
        "test_name": "hardcoded_tmp_directory",
        "issue_severity": "MEDIUM",
        "issue_confidence": "MEDIUM",
        "issue_text": "Probable insecure usage of temp file/directory.",
        "line_number": 12,
    }
    assert BanditAnalyzer._to_finding(item, "tests/conftest.py") is None
    assert BanditAnalyzer._to_finding(item, "src/io.py") is not None


def test_bandit_still_reports_real_issues_in_test_files():
    """豁免仅限惯例用法：真实安全问题在测试文件中依然报告。"""
    item = {
        "test_id": "B602",
        "test_name": "subprocess_popen_with_shell_equals_true",
        "issue_severity": "HIGH",
        "issue_confidence": "HIGH",
        "issue_text": "subprocess call with shell=True identified.",
        "line_number": 8,
    }
    finding = BanditAnalyzer._to_finding(item, "tests/test_integration.py")
    assert finding is not None
    assert finding.rule_id == "B602"


def test_radon_exempts_test_files():
    """测试函数因大量断言天然高复杂度，不报复杂度告警。"""
    source = "def test_choices():\n    if a:\n        pass\n" * 20  # 构造高 CC
    analyzer = RadonAnalyzer()
    if not analyzer.is_available():
        return  # 未安装 radon 时跳过
    assert analyzer.run("tests/test_many.py", source) == []


def test_radon_reports_production_code():
    analyzer = RadonAnalyzer()
    if not analyzer.is_available():
        return
    branches = "".join(f"    if x{i}:\n        pass\n" for i in range(25))
    source = f"def complex_fn(x0=0):\n{branches}"
    findings = analyzer.run("src/logic.py", source)
    assert findings and findings[0].rule_id == "radon-cc"

"""去重策略测试：跨来源合并、同来源保留、优先级顺序。

背景：demo_target 演示中发现 Bandit 的英文发现（B307 blacklist）
把自研规则的中文发现（CG011 eval，含具体修复建议）挤掉了——
本文件锁定修复后的去重行为。
"""

from codesage.core.models import (
    Category,
    Finding,
    Severity,
    Source,
    dedup_findings,
)


def make(
    source: Source,
    *,
    line: int = 10,
    category: Category = Category.SECURITY,
    title: str = "x",
    confidence: float = 0.9,
    rule_id: str = "",
) -> Finding:
    return Finding(
        file="a.py",
        line_start=line,
        line_end=line,
        category=category,
        severity=Severity.MAJOR,
        title=title,
        confidence=confidence,
        source=source,
        rule_id=rule_id,
    )


def test_cross_source_dedup_keeps_highest_priority():
    """同一位置跨工具重复 → 保留优先级最高来源（自研规则 > Bandit）。"""
    bandit = make(Source.BANDIT, title="B307 blacklist", rule_id="B307")
    rule = make(Source.AST_RULE, title="使用了 eval()", rule_id="CG011")
    result = dedup_findings([bandit, rule])
    assert len(result) == 1
    assert result[0].source == Source.AST_RULE
    assert result[0].rule_id == "CG011"


def test_same_source_findings_all_kept():
    """同一来源在同一位置的多个发现是不同问题，全部保留。"""
    bare_except = make(Source.AST_RULE, title="裸 except", rule_id="CG002")
    swallowed = make(Source.AST_RULE, title="异常被静默吞没", rule_id="CG003")
    result = dedup_findings([bare_except, swallowed])
    assert len(result) == 2


def test_llm_beats_all_sources():
    """LLM 语义确认过的发现优先级最高。"""
    llm = make(Source.LLM, confidence=0.75)
    rule = make(Source.AST_RULE, confidence=1.0)
    bandit = make(Source.BANDIT, confidence=0.95)
    result = dedup_findings([rule, bandit, llm])
    assert len(result) == 1
    assert result[0].source == Source.LLM


def test_different_locations_not_merged():
    a = make(Source.AST_RULE, line=10)
    b = make(Source.AST_RULE, line=50)
    assert len(dedup_findings([a, b])) == 2


def test_different_categories_not_merged():
    """同一行不同类别（如 bug 与 security）是两个独立问题。"""
    bug = make(Source.AST_RULE, line=14, category=Category.BUG)
    security = make(Source.BANDIT, line=14, category=Category.SECURITY)
    assert len(dedup_findings([bug, security])) == 2


def test_finding_language_roundtrip():
    """language 字段必须进 to_dict / from_dict：报告导出与历史页都读它。"""
    finding = Finding(
        file="src/main/java/Foo.java",
        line_start=3,
        line_end=3,
        category=Category.BUG,
        severity=Severity.MAJOR,
        title="空 catch",
        source=Source.JAVA_RULE,
        rule_id="JG001",
        language="java",
    )
    payload = finding.to_dict()
    assert payload["language"] == "java"
    assert payload["source"] == "java_rule"

    restored = Finding.from_dict(payload)
    assert restored.language == "java"
    assert restored.source is Source.JAVA_RULE


def test_finding_language_inferred_from_path():
    """LLM 提交的发现不会填 language——按文件后缀推断，导出围栏才正确。"""
    finding = Finding(
        file="src/App.java",
        line_start=1,
        line_end=1,
        category=Category.BUG,
        severity=Severity.MINOR,
        title="x",
    )
    assert finding.language == ""
    assert finding.language_code == "java"

    # 完全无法判断时回退 python（历史 payload 没有该字段，不能崩）
    unknown = Finding(
        file="Makefile",
        line_start=1,
        line_end=1,
        category=Category.QUALITY,
        severity=Severity.INFO,
        title="x",
    )
    assert unknown.language_code == "python"


def test_unknown_source_is_tolerated():
    """旧版本读到新版本写入的来源时降级，而不是抛 ValueError 让历史页白屏。"""
    payload = {
        "file": "a.java",
        "line_start": 1,
        "category": "bug",
        "severity": "major",
        "title": "x",
        "source": "checkstyle",  # 尚未注册的未来来源
    }
    finding = Finding.from_dict(payload)
    assert finding.source is Source.AST_RULE
    assert finding.language == "java"


def test_enum_chinese_labels_never_raise():
    """枚举是增量扩展的：新增值漏配中文名应退化为英文值，不能 KeyError。"""
    for severity in Severity:
        assert severity.zh
        assert severity.label.startswith("P")
    for category in Category:
        assert category.zh
    for source in Source:
        assert source.zh
    assert Source.JAVA_RULE.zh == "Java 规则"


def test_java_rule_dedup_priority_ties_with_ast_rule():
    """两种自研规则同级：语言互斥，不会真正撞在同一行上。"""
    java = make(Source.JAVA_RULE)
    bandit = make(Source.BANDIT)
    result = dedup_findings([bandit, java])
    assert len(result) == 1
    assert result[0].source is Source.JAVA_RULE


def test_report_to_dict_from_dict_roundtrip():
    """报告序列化往返：历史详情渲染依赖 from_dict 的完整性。"""
    from codesage.core.models import ReviewReport, TokenUsage

    report = ReviewReport(target="/proj/x")
    report.findings = [make(Source.LLM, title="发现问题")]
    report.usage = TokenUsage(input_tokens=100, output_tokens=50, calls=3)
    report.turns = 4
    report.llm_enabled = True
    report.stop_reason = "模型主动结束"
    report.files_scanned = 7
    report.metadata = {"profile": "full", "cost_yuan": 0.0123}

    restored = ReviewReport.from_dict(report.to_dict())

    assert restored.target == "/proj/x"
    assert restored.score == report.score
    assert len(restored.findings) == 1
    assert restored.findings[0].title == "发现问题"
    assert restored.findings[0].source == Source.LLM
    assert restored.usage.input_tokens == 100
    assert restored.usage.calls == 3
    assert restored.turns == 4
    assert restored.llm_enabled is True
    assert restored.stop_reason == "模型主动结束"
    assert restored.files_scanned == 7
    assert restored.metadata["profile"] == "full"

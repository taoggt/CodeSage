"""Java 自研规则测试：每条规则正反例成对。

为什么成对：单测"能报出问题"只能证明召回，证明不了精度。误报是代码审查工具
最致命的失败模式（开发者会直接关掉它），所以每条规则都必须有一条"正确写法
不应报"的断言。

隔离方式：每个用例只给引擎一条规则，避免其他规则的命中干扰断言。
"""

from __future__ import annotations

import pytest

from codesage.analyzers import java_parser
from codesage.analyzers.java_rules import (
    JAVA_RULE_IDS,
    BroadCatchRule,
    CommandExecutionRule,
    ComplexityRule,
    EmptyCatchRule,
    EqualsWithoutHashCodeRule,
    HardcodedCredentialRule,
    JavaRuleEngine,
    SqlConcatenationRule,
    StaticSimpleDateFormatRule,
    StringConcatInLoopRule,
    UnclosedResourceRule,
    default_java_rules,
)
from codesage.core.models import Category, Severity

pytestmark = pytest.mark.skipif(
    not java_parser.is_available(),
    reason='未安装 tree-sitter-java（pip install -e ".[java]"）',
)


def run_rule(rule, source: str, filename: str = "Demo.java"):
    return JavaRuleEngine([rule]).run(filename, source)


def ids(findings):
    return [f.rule_id for f in findings]


# ---------------------------------------------------------------- JG001


def test_jg001_empty_catch():
    findings = run_rule(
        EmptyCatchRule(),
        "class A { void f() { try { g(); } catch (IllegalStateException e) { } } }",
    )
    assert ids(findings) == ["JG001"]
    assert findings[0].severity is Severity.MAJOR
    assert findings[0].language == "java"


def test_jg001_comment_only_catch():
    findings = run_rule(
        EmptyCatchRule(),
        "class A { void f() { try { g(); } catch (Exception e) { /* 忽略 */ } } }",
    )
    assert ids(findings) == ["JG001"]


def test_jg001_handled_catch_not_reported():
    findings = run_rule(
        EmptyCatchRule(),
        "class A { void f() { try { g(); } catch (IllegalStateException e) { log(e); } } }",
    )
    assert findings == []


# ---------------------------------------------------------------- JG002


def test_jg002_throwable_is_major():
    findings = run_rule(
        BroadCatchRule(),
        "class A { void f() { try { g(); } catch (Throwable t) { log(t); } } }",
    )
    assert ids(findings) == ["JG002"]
    assert findings[0].severity is Severity.MAJOR
    assert findings[0].category is Category.BUG


def test_jg002_exception_is_minor():
    findings = run_rule(
        BroadCatchRule(),
        "class A { void f() { try { g(); } catch (Exception e) { log(e); } } }",
    )
    assert ids(findings) == ["JG002"]
    assert findings[0].severity is Severity.MINOR


def test_jg002_specific_exception_not_reported():
    findings = run_rule(
        BroadCatchRule(),
        "class A { void f() { try { g(); } catch (NumberFormatException e) { log(e); } } }",
    )
    assert findings == []


# ---------------------------------------------------------------- JG003


def test_jg003_dynamic_exec_is_major():
    findings = run_rule(
        CommandExecutionRule(),
        'class A { void f(String cmd) throws Exception { Runtime.getRuntime().exec("sh -c " + cmd); } }',
    )
    assert ids(findings) == ["JG003"]
    assert findings[0].severity is Severity.MAJOR
    assert findings[0].category is Category.SECURITY


def test_jg003_literal_exec_is_info():
    """全字面量的 exec 不构成注入，降级为 INFO 但保留提示。"""
    findings = run_rule(
        CommandExecutionRule(),
        'class A { void f() throws Exception { Runtime.getRuntime().exec("ls -l"); } }',
    )
    assert ids(findings) == ["JG003"]
    assert findings[0].severity is Severity.INFO


def test_jg003_process_builder_dynamic():
    findings = run_rule(
        CommandExecutionRule(),
        "class A { void f(String[] args) throws Exception { new ProcessBuilder(args).start(); } }",
    )
    assert ids(findings) == ["JG003"]
    assert findings[0].severity is Severity.MAJOR


def test_jg003_ordinary_call_not_reported():
    findings = run_rule(
        CommandExecutionRule(),
        "class A { void f() { service.exec(); } }",
    )
    assert findings == []


# ---------------------------------------------------------------- JG004


def test_jg004_hardcoded_secret():
    findings = run_rule(
        HardcodedCredentialRule(),
        'class A { private static final String apiKey = "a1b2c3d4e5f6"; }',
    )
    assert ids(findings) == ["JG004"]
    assert findings[0].category is Category.SECURITY


def test_jg004_env_lookup_not_reported():
    findings = run_rule(
        HardcodedCredentialRule(),
        'class A { private String apiKey = System.getenv("API_KEY"); }',
    )
    assert findings == []


def test_jg004_plain_word_value_not_reported():
    """纯小写单词多是"字段名回填"或占位，误报成本高于收益。"""
    findings = run_rule(
        HardcodedCredentialRule(),
        'class A { private String password = "password"; }',
    )
    assert findings == []


def test_jg004_placeholder_not_reported():
    findings = run_rule(
        HardcodedCredentialRule(),
        'class A { private String token = "YOUR_TOKEN_1"; }',
    )
    assert findings == []


# ---------------------------------------------------------------- JG005


def test_jg005_sql_concatenation_direct():
    findings = run_rule(
        SqlConcatenationRule(),
        'class A { void f(String id) throws Exception { '
        'stmt.executeQuery("SELECT * FROM t WHERE id = " + id); } }',
    )
    assert ids(findings) == ["JG005"]
    assert findings[0].category is Category.SECURITY


def test_jg005_sql_concatenation_via_variable():
    """先拼到局部变量再执行是最常见写法，必须能回查同一方法内的声明。"""
    findings = run_rule(
        SqlConcatenationRule(),
        "class A { void f(String id) throws Exception { "
        'String sql = "SELECT name FROM t WHERE id = " + id; '
        "stmt.executeQuery(sql); } }",
    )
    assert ids(findings) == ["JG005"]


def test_jg005_parameterized_query_not_reported():
    findings = run_rule(
        SqlConcatenationRule(),
        'class A { void f(String id) throws Exception { '
        'stmt.executeQuery("SELECT * FROM t WHERE id = ?"); } }',
    )
    assert findings == []


# ---------------------------------------------------------------- JG006


def test_jg006_resource_outside_try():
    findings = run_rule(
        UnclosedResourceRule(),
        "class A { void f(String p) throws Exception { "
        "FileInputStream in = new FileInputStream(p); in.read(); } }",
    )
    assert ids(findings) == ["JG006"]
    assert findings[0].category is Category.BUG


def test_jg006_try_with_resources_not_reported():
    findings = run_rule(
        UnclosedResourceRule(),
        "class A { void f(String p) throws Exception { "
        "try (FileInputStream in = new FileInputStream(p)) { in.read(); } } }",
    )
    assert findings == []


def test_jg006_non_resource_type_not_reported():
    findings = run_rule(
        UnclosedResourceRule(),
        "class A { void f() { StringBuilder sb = new StringBuilder(); } }",
    )
    assert findings == []


# ---------------------------------------------------------------- JG007


def test_jg007_string_concat_in_loop():
    findings = run_rule(
        StringConcatInLoopRule(),
        "class A { String f(String[] rows) { String s = \"\"; "
        "for (String row : rows) { s += row; } return s; } }",
    )
    assert ids(findings) == ["JG007"]
    assert findings[0].category is Category.PERFORMANCE


def test_jg007_string_literal_rhs_in_loop():
    findings = run_rule(
        StringConcatInLoopRule(),
        'class A { String f() { String s = ""; for (int i = 0; i < 3; i++) { s += "x"; } return s; } }',
    )
    assert ids(findings) == ["JG007"]


def test_jg007_numeric_accumulation_not_reported():
    findings = run_rule(
        StringConcatInLoopRule(),
        "class A { int f(int n) { int total = 0; for (int i = 0; i < n; i++) { total += i; } return total; } }",
    )
    assert findings == []


def test_jg007_outside_loop_not_reported():
    findings = run_rule(
        StringConcatInLoopRule(),
        'class A { String f() { String s = ""; s += "x"; return s; } }',
    )
    assert findings == []


# ---------------------------------------------------------------- JG008


def test_jg008_static_simple_date_format():
    findings = run_rule(
        StaticSimpleDateFormatRule(),
        'class A { private static final SimpleDateFormat F = new SimpleDateFormat("yyyy"); }',
    )
    assert ids(findings) == ["JG008"]
    assert findings[0].severity is Severity.MAJOR


def test_jg008_instance_field_not_reported():
    findings = run_rule(
        StaticSimpleDateFormatRule(),
        'class A { private final SimpleDateFormat f = new SimpleDateFormat("yyyy"); }',
    )
    assert findings == []


def test_jg008_datetime_formatter_not_reported():
    findings = run_rule(
        StaticSimpleDateFormatRule(),
        "class A { private static final DateTimeFormatter F = DateTimeFormatter.ISO_DATE; }",
    )
    assert findings == []


# ---------------------------------------------------------------- JG009


def test_jg009_equals_without_hashcode():
    findings = run_rule(
        EqualsWithoutHashCodeRule(),
        "class A { @Override public boolean equals(Object o) { return false; } }",
    )
    assert ids(findings) == ["JG009"]


def test_jg009_both_overridden_not_reported():
    findings = run_rule(
        EqualsWithoutHashCodeRule(),
        "class A { @Override public boolean equals(Object o) { return false; } "
        "@Override public int hashCode() { return 0; } }",
    )
    assert findings == []


def test_jg009_equals_with_other_param_not_reported():
    findings = run_rule(
        EqualsWithoutHashCodeRule(),
        "class A { public boolean equals(A other) { return false; } }",
    )
    assert findings == []


# ---------------------------------------------------------------- JGCC


_COMPLEX_METHOD = "class A { int f(int n) { int t = 0; " + "".join(
    f"if (n > {i}) {{ t++; }} " for i in range(10)
) + "return t; } }"


def test_jgcc_reports_complex_method():
    findings = run_rule(ComplexityRule(), _COMPLEX_METHOD)
    assert ids(findings) == ["JGCC"]
    assert findings[0].severity is Severity.MINOR
    assert "CC=" in findings[0].title


def test_jgcc_skips_simple_method():
    findings = run_rule(ComplexityRule(), "class A { int f() { return 1; } }")
    assert findings == []


# ---------------------------------------------------------------- 引擎整体


def test_rule_ids_are_stable_and_unique():
    assert JAVA_RULE_IDS == [
        "JG001", "JG002", "JG003", "JG004", "JG005",
        "JG006", "JG007", "JG008", "JG009", "JGCC",
    ]


def test_engine_guards_non_java_language():
    """双保险：即使上游误把 .py 送进来，Java 引擎也必须沉默。"""
    findings = JavaRuleEngine().run(
        "app.py", "def f(x=[]):\n    try:\n        pass\n    except:\n        pass\n"
    )
    assert findings == []


def test_engine_skips_noise_sensitive_rules_in_tests():
    """测试代码里硬编码凭据与高复杂度是惯例，不报；空 catch 照报。"""
    source = (
        "class FooTest { "
        'private String token = "a1b2c3d4e5f6"; '
        "void f() { try { g(); } catch (IllegalStateException e) { } } }"
    )
    findings = JavaRuleEngine().run("src/test/java/FooTest.java", source)
    assert "JG001" in ids(findings)
    assert "JG004" not in ids(findings)


def test_engine_reports_test_file_by_main_path_marker():
    """src/test/java 之外的 *Test.java 也应被识别为测试代码。"""
    source = 'class FooTest { private String token = "a1b2c3d4e5f6"; }'
    findings = JavaRuleEngine().run("app/FooTest.java", source)
    assert findings == []


def test_engine_survives_broken_rule(monkeypatch):
    """单条规则抛异常不能拖垮整份报告。"""

    class Boom(EmptyCatchRule):
        id = "JGBOOM"

        def check(self, ctx):  # noqa: ANN001, D401
            raise RuntimeError("boom")

    findings = JavaRuleEngine([Boom(), EmptyCatchRule()]).run(
        "A.java", "class A { void f() { try { g(); } catch (IllegalStateException e) { } } }"
    )
    assert ids(findings) == ["JG001"]


def test_default_rule_set_covers_all_ids():
    assert [rule.id for rule in default_java_rules()] == JAVA_RULE_IDS

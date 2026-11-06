"""tree-sitter-java 封装测试。

重点锁三件容易错的事：
1. 行号必须 1-based（tree-sitter 原生是 0-based，漏加 1 会让所有 finding 偏一行）；
2. 语法错误返回 None 而不是抛异常（Java 半成品文件很常见）；
3. 依赖缺失时整条链路降级而不崩（Windows 上 wheel 装不上是真实场景）。
"""

from __future__ import annotations

import pytest

from codesage.analyzers import java_parser

SAMPLE = """package com.demo;

public class Demo extends Base implements IFoo {
    private int limit = 10;

    public int compute(int a) throws IllegalStateException {
        int total = 0;
        for (int i = 0; i < a; i++) {
            if (i % 2 == 0 && a > 3) {
                total += i;
            } else {
                total -= i;
            }
        }
        try {
            total = total / a;
        } catch (ArithmeticException e) {
            total = 0;
        }
        return total > 0 ? total : 0;
    }
}
"""


pytestmark = pytest.mark.skipif(
    not java_parser.is_available(),
    reason='未安装 tree-sitter-java（pip install -e ".[java]"）',
)


def test_parse_returns_program_tree():
    tree = java_parser.parse_java("class A { void f() {} }")
    assert tree is not None
    assert tree.root.type == "program"
    assert not tree.has_error


def test_line_numbers_are_one_based():
    tree = java_parser.parse_java(SAMPLE)
    assert tree is not None
    declarations = list(java_parser.iter_type_declarations(tree))
    assert len(declarations) == 1
    # 第 3 行是 "public class Demo ..."
    assert java_parser.line_of(declarations[0]) == 3


def test_syntax_error_returns_none_by_default():
    """解析失败 ≠ 代码有问题：不产 CG000 式的假语法错误。"""
    assert java_parser.parse_java("class Broken { void f( }") is None


def test_syntax_error_can_be_parsed_for_outline():
    tree = java_parser.parse_java("class Broken { void f( }", allow_errors=True)
    assert tree is not None
    assert tree.has_error


def test_oversized_source_is_skipped(monkeypatch):
    monkeypatch.setattr(java_parser, "MAX_PARSE_BYTES", 8)
    assert java_parser.parse_java("class A { void f() {} }") is None


def test_empty_source_returns_none():
    assert java_parser.parse_java("") is None


def test_outline_contains_class_and_signature():
    outline = java_parser.build_java_outline(SAMPLE)
    assert outline, "大纲不应为空"
    assert outline[0].startswith("L3 ")
    assert "class Demo" in outline[0]
    assert "implements IFoo" in outline[0]
    method_line = next(line for line in outline if "compute" in line)
    assert method_line.strip().startswith("L6 ")
    assert "public int compute(int a)" in method_line
    assert "throws IllegalStateException" in method_line


def test_cyclomatic_complexity_counts_decision_points():
    tree = java_parser.parse_java(SAMPLE)
    assert tree is not None
    method = next(java_parser.iter_methods(tree.root))
    # 基准 1 + for + if + && + catch + 三元 = 6
    assert java_parser.cyclomatic_complexity(tree, method) == 6


def test_trivial_method_has_complexity_one():
    tree = java_parser.parse_java("class A { int f() { return 1; } }")
    assert tree is not None
    method = next(java_parser.iter_methods(tree.root))
    assert java_parser.cyclomatic_complexity(tree, method) == 1


def test_declared_type_and_signature_helpers():
    tree = java_parser.parse_java(SAMPLE)
    assert tree is not None
    declaration = next(java_parser.iter_type_declarations(tree))
    body = declaration.child_by_field_name("body")
    method = next(java_parser.iter_methods(body))
    assert java_parser.qualified_method_name(tree, method) == "Demo.compute"
    # Node 每次访问都会新建包装对象，不能比 is；比位置
    enclosing = java_parser.enclosing_type(method)
    assert enclosing is not None
    assert (enclosing.start_byte, enclosing.type) == (
        declaration.start_byte,
        declaration.type,
    )


def test_missing_dependency_degrades_gracefully(monkeypatch):
    """依赖缺失时必须整体降级：is_available False、parse 返回 None、不抛异常。"""
    monkeypatch.setattr(java_parser, "_module_available", lambda name: False)
    java_parser.reset_cache()
    try:
        assert java_parser.is_available() is False
        assert java_parser.parse_java("class A {}") is None
        assert java_parser.build_java_outline("class A {}") == []
    finally:
        java_parser.reset_cache()

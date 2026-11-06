"""按声明边界切块。

为什么不用滑动窗口：
- 滑动窗口会把函数拦腰切断，模型看到的上下文语义是残的；
- 按语法树的函数/类边界切块，每块语义完整，且能带上符号名（供 prompt 引用）。

用途：
- 大文件的细粒度处理（超过 max_chunk_lines 的函数再按行窗口细分）
- 生成文件大纲（符号清单）

状态说明（诚实标注）：`chunk_source` **目前没有被 pipeline 调用**，只有测试在用。
Agent 的读码路径是 read_file 行窗口 + list_symbols。多语言改造这里只做
"接口能按语言分派"，不接线——接线会新增一条未经 Prompt 验证的组装分支，
对"能审 Java"零收益却扩大回归面。真正启用排在阶段二。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from codesage.core.languages import Language, language_for_path


@dataclass
class CodeChunk:
    """一个代码块。"""

    file: str
    start_line: int  # 1-based，含
    end_line: int    # 1-based，含
    symbol: str      # 符号名；module 级代码为 "<module>"
    kind: str        # function / class / method / module / window
    source: str = ""

    @property
    def line_count(self) -> int:
        return self.end_line - self.start_line + 1


def _split_long(start: int, end: int, symbol: str, kind: str, max_lines: int) -> list[tuple[int, int, str, str]]:
    """把超长区间切成多个行窗口。"""
    spans: list[tuple[int, int, str, str]] = []
    cur = start
    idx = 1
    while cur <= end:
        chunk_end = min(cur + max_lines - 1, end)
        spans.append((cur, chunk_end, f"{symbol}#part{idx}", "window"))
        cur = chunk_end + 1
        idx += 1
    return spans


def _whole_file(filename: str, total_lines: int) -> list[CodeChunk]:
    return [
        CodeChunk(
            file=filename,
            start_line=1,
            end_line=total_lines,
            symbol="<module>",
            kind="module",
        )
    ]


def _python_spans(tree: ast.AST, total_lines: int, max_lines: int) -> list[tuple[int, int, str, str]]:
    spans: list[tuple[int, int, str, str]] = []
    cursor = 1  # 下一个未归属的行

    defs = [
        node
        for node in tree.body  # type: ignore[attr-defined]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]

    for node in defs:
        start = node.lineno
        # 装饰器行也要包含在块内
        if getattr(node, "decorator_list", None):
            start = min(d.lineno for d in node.decorator_list)
        end = getattr(node, "end_lineno", None) or start
        if start > cursor:
            spans.append((cursor, start - 1, "<module>", "module"))
        kind = "class" if isinstance(node, ast.ClassDef) else "function"
        if end - start + 1 > max_lines:
            spans.extend(_split_long(start, end, node.name, kind, max_lines))
        else:
            spans.append((start, end, node.name, kind))
        cursor = end + 1

    if cursor <= total_lines:
        spans.append((cursor, total_lines, "<module>", "module"))
    return spans


def _java_spans(source: str, total_lines: int, max_lines: int) -> list[tuple[int, int, str, str]]:
    """Java：按类型声明 / 方法边界切块（依赖 tree-sitter，缺失时退回整块）。"""
    from codesage.analyzers import java_parser

    tree = java_parser.parse_java(source, allow_errors=True)
    if tree is None:
        return [(1, total_lines, "<module>", "module")]

    spans: list[tuple[int, int, str, str]] = []
    for declaration in java_parser.iter_type_declarations(tree):
        start = java_parser.line_of(declaration)
        end = java_parser.end_line_of(declaration)
        name = java_parser.child_text(tree, declaration, "name") or "<anonymous>"
        if end - start + 1 > max_lines:
            spans.extend(_split_long(start, end, name, "class", max_lines))
        else:
            spans.append((start, end, name, "class"))
    return spans or [(1, total_lines, "<module>", "module")]


def chunk_source(
    filename: str,
    source: str,
    max_chunk_lines: int = 400,
    language: Language | None = None,
) -> list[CodeChunk]:
    """把源码切成语义块。

    策略：
    1. 解析失败（语法错误）→ 返回单块（交给上层决定是否报语法错误）；
    2. 文件不超过 max_chunk_lines → 返回单块（symbol=<module>）；
    3. 否则按顶层函数/类切块，间隙（import、模块级代码）归入 <module> 块；
    4. 超过 max_chunk_lines 的函数再按行窗口细分。

    `language` 缺省时按文件后缀推断（保持旧调用点单参可用）。
    """
    total_lines = source.count("\n") + 1
    if language is None:
        language = language_for_path(filename) or Language.PYTHON

    if total_lines <= max_chunk_lines:
        return _whole_file(filename, total_lines)

    if language is Language.JAVA:
        spans = _java_spans(source, total_lines, max_chunk_lines)
    else:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return _whole_file(filename, total_lines)
        spans = _python_spans(tree, total_lines, max_chunk_lines)

    return [
        CodeChunk(
            file=filename,
            start_line=s,
            end_line=e,
            symbol=symbol,
            kind=kind,
        )
        for (s, e, symbol, kind) in spans
        if e >= s
    ]

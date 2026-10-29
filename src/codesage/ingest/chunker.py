"""按函数边界切块。

为什么不用滑动窗口：
- 滑动窗口会把函数拦腰切断，模型看到的上下文语义是残的；
- 按 AST 的函数/类边界切块，每块语义完整，且能带上符号名（供 prompt 引用）。

用途：
- 大文件的细粒度处理（超过 max_chunk_lines 的函数再按行窗口细分）
- 生成文件大纲（符号清单）
"""

from __future__ import annotations

import ast
from dataclasses import dataclass


@dataclass
class CodeChunk:
    """一个代码块。"""

    file: str
    start_line: int  # 1-based，含
    end_line: int    # 1-based，含
    symbol: str      # 符号名；module 级代码为 "<module>"
    kind: str        # function / class / module / window
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


def chunk_source(
    filename: str,
    source: str,
    max_chunk_lines: int = 400,
) -> list[CodeChunk]:
    """把源码切成语义块。

    策略：
    1. 解析失败（语法错误）→ 返回单块（交给上层决定是否报语法错误）；
    2. 文件不超过 max_chunk_lines → 返回单块（symbol=<module>）；
    3. 否则按顶层函数/类切块，间隙（import、模块级代码）归入 <module> 块；
    4. 超过 max_chunk_lines 的函数再按行窗口细分。
    """
    total_lines = source.count("\n") + 1

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return [
            CodeChunk(
                file=filename,
                start_line=1,
                end_line=total_lines,
                symbol="<module>",
                kind="module",
            )
        ]

    if total_lines <= max_chunk_lines:
        return [
            CodeChunk(
                file=filename,
                start_line=1,
                end_line=total_lines,
                symbol="<module>",
                kind="module",
            )
        ]

    spans: list[tuple[int, int, str, str]] = []
    cursor = 1  # 下一个未归属的行

    defs = [
        node
        for node in tree.body
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
        if end - start + 1 > max_chunk_lines:
            spans.extend(_split_long(start, end, node.name, kind, max_chunk_lines))
        else:
            spans.append((start, end, node.name, kind))
        cursor = end + 1

    if cursor <= total_lines:
        spans.append((cursor, total_lines, "<module>", "module"))

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

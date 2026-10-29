"""list_symbols 工具：文件符号大纲（低成本了解文件结构）。"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Callable

from codesage.agent.registry import tool
from codesage.tools.read_file import _resolve_inside


def _format_args(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = node.args
    names: list[str] = []
    for arg in args.posonlyargs + args.args:
        names.append(arg.arg)
    if args.vararg:
        names.append(f"*{args.vararg.arg}")
    for arg in args.kwonlyargs:
        names.append(arg.arg)
    if args.kwarg:
        names.append(f"**{args.kwarg.arg}")
    return ", ".join(names)


def _format_returns(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    if node.returns is not None:
        try:
            return f" -> {ast.unparse(node.returns)}"
        except Exception:  # noqa: BLE001
            return ""
    return ""


def make_list_symbols_tool(workspace: Path) -> Callable[..., str]:
    root = Path(workspace).resolve()

    @tool(name="list_symbols")
    def list_symbols(path: str) -> str:
        """列出文件的符号大纲（类 / 函数 / 方法签名 + 行号）。

        Args:
            path: 相对工作区路径
        """
        target = _resolve_inside(root, path)
        if target is None or not target.is_file():
            return f"ERROR: 文件不存在或路径非法：{path}"
        try:
            source = target.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source)
        except SyntaxError as exc:
            return f"ERROR: 语法错误无法解析（line {exc.lineno}）：{exc.msg}"
        except OSError as exc:
            return f"ERROR: 读取失败：{exc}"

        lines: list[str] = []
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                bases = ", ".join(
                    ast.unparse(b) for b in node.bases if not isinstance(b, ast.Name)
                )
                suffix = f"({bases})" if bases else ""
                lines.append(f"L{node.lineno} class {node.name}{suffix}")
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        lines.append(
                            f"  L{item.lineno} def {item.name}"
                            f"({_format_args(item)}){_format_returns(item)}"
                        )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                lines.append(
                    f"L{node.lineno} def {node.name}"
                    f"({_format_args(node)}){_format_returns(node)}"
                )

        if not lines:
            return "（文件中没有类或函数定义）"
        header = f"# {path} 符号大纲（{len(lines)} 项）"
        return "\n".join([header, *lines])

    return list_symbols

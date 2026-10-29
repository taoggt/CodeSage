"""read_file 工具：读取文件内容（带行号、行数上限、路径越权防护）。"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from codesage.agent.registry import tool


def _resolve_inside(root: Path, path: str) -> Path | None:
    """把相对路径解析到工作区内；越权或非法路径返回 None（防路径穿越）。"""
    try:
        target = (root / path).resolve()
    except (OSError, ValueError):
        return None
    try:
        if not target.is_relative_to(root):
            return None
    except ValueError:
        return None
    return target


def make_read_file_tool(workspace: Path, limit: int = 500) -> Callable[..., str]:
    """创建 read_file 工具。

    limit 是单次返回的最大行数——从源头控制上下文增长，
    防止模型一次读取就把上下文撑爆。
    """
    root = Path(workspace).resolve()

    @tool(name="read_file")
    def read_file(path: str, start_line: int = 1, end_line: int = 0) -> str:
        """读取工作区内文件的内容（带行号，单次最多返回若干行）。

        Args:
            path: 相对工作区根目录的文件路径
            start_line: 起始行号（1-based，默认从第 1 行开始）
            end_line: 结束行号（0 表示自动读取 start_line 之后的若干行）
        """
        target = _resolve_inside(root, path)
        if target is None:
            return f"ERROR: 非法路径（必须是工作区内的文件）：{path}"
        if not target.is_file():
            return f"ERROR: 文件不存在：{path}"

        try:
            all_lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            return f"ERROR: 读取失败：{exc}"

        total = len(all_lines)
        if total == 0:
            return f"ERROR: 文件为空：{path}"

        start = max(1, int(start_line))
        if int(end_line) >= start:
            end = min(int(end_line), total)
        else:
            end = min(start + limit - 1, total)

        if start > total:
            return f"ERROR: start_line={start} 超过文件总行数（{total}）"

        width = len(str(end))
        out = [f"# {path}（共 {total} 行，当前显示 {start}-{end}）"]
        for i in range(start, end + 1):
            out.append(f"{i:>{width}} | {all_lines[i - 1]}")
        if end < total:
            out.append(f"...（还有 {total - end} 行未显示，可用 start_line={end + 1} 继续读取）")
        return "\n".join(out)

    return read_file

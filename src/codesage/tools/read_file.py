"""read_file 工具：读取文件内容（带行号、行数上限、路径越权防护）。"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from codesage.agent.registry import tool
from codesage.tools.paths import normalize_relative, resolve_inside


def make_read_file_tool(
    workspace: Path,
    limit: int = 500,
    visit_log: list[str] | None = None,
) -> Callable[..., str]:
    """创建 read_file 工具。

    limit 是单次返回的最大行数——从源头控制上下文增长，
    防止模型一次读取就把上下文撑爆。

    visit_log 是可选的「已读文件台账」：成功读取时追加规范化相对路径。
    由 pipeline 持有，用于压缩时确定性重建「已审查文件清单」与计算覆盖率。
    工具层只负责往列表里追加，不知道台账最终被谁消费。
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
        target = resolve_inside(root, str(path))
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

        rel = normalize_relative(target, root)
        if visit_log is not None:
            visit_log.append(rel)

        width = len(str(end))
        out = [f"# {rel}（共 {total} 行，当前显示 {start}-{end}）"]
        for i in range(start, end + 1):
            out.append(f"{i:>{width}} | {all_lines[i - 1]}")
        if end < total:
            out.append(f"...（还有 {total - end} 行未显示，可用 start_line={end + 1} 继续读取）")
        return "\n".join(out)

    return read_file

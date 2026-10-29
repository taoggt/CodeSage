"""run_analyzer 工具：按需触发确定性分析器（零 Token，结果结构化返回）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from codesage.agent.registry import tool
from codesage.tools.read_file import _resolve_inside

MAX_ITEMS = 30  # 单次返回的问题条数上限（防刷屏）


def make_run_analyzer_tool(
    analyzers: list[Any],
    workspace: Path,
) -> Callable[..., str]:
    root = Path(workspace).resolve()
    by_name = {a.name: a for a in analyzers}
    available = ", ".join(sorted(by_name)) if by_name else "（无）"

    @tool(name="run_analyzer", description=f"对指定文件运行确定性分析器（零成本）。可用分析器：{available}")
    def run_analyzer(analyzer: str, path: str) -> str:
        """对指定文件运行确定性分析器。

        Args:
            analyzer: 分析器名称（ruff / bandit / radon）
            path: 相对工作区路径（.py 文件）
        """
        analyzer_name = str(analyzer).strip().lower()
        tool_obj = by_name.get(analyzer_name)
        if tool_obj is None:
            return f"ERROR: 未知分析器 '{analyzer}'。可用：{available}"
        if not tool_obj.is_available():
            return f"ERROR: 分析器 {analyzer_name} 当前不可用（未安装）"

        target = _resolve_inside(root, path)
        if target is None or not target.is_file():
            return f"ERROR: 文件不存在或路径非法：{path}"

        try:
            source = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return f"ERROR: 读取失败：{exc}"

        findings = tool_obj.run(path, source)
        if not findings:
            return f"{analyzer_name} 未发现问题：{path}"

        lines = [f"{analyzer_name} 在 {path} 发现 {len(findings)} 个问题："]
        for f in findings[:MAX_ITEMS]:
            lines.append(
                f"- [{f.severity.label}] {f.file}:{f.line_start} "
                f"[{f.rule_id}] {f.title}"
                + (f" | {f.description[:100]}" if f.description else "")
            )
        if len(findings) > MAX_ITEMS:
            lines.append(f"...（另有 {len(findings) - MAX_ITEMS} 条未列出）")
        return "\n".join(lines)

    return run_analyzer

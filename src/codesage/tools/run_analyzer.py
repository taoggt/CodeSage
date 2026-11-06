"""run_analyzer 工具：按需触发确定性分析器（零 Token，结果结构化返回）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from codesage.agent.registry import tool
from codesage.core.languages import language_for_path, language_spec
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
            analyzer: 分析器名称（Python：ruff / bandit / radon；Java：java_rule）
            path: 相对工作区路径（.py 或 .java）
        """
        analyzer_name = str(analyzer).strip().lower()
        tool_obj = by_name.get(analyzer_name)
        if tool_obj is None:
            return f"ERROR: 未知分析器 '{analyzer}'。可用：{available}"
        if not tool_obj.is_available():
            return f"ERROR: 分析器 {analyzer_name} 当前不可用（未安装）"

        # 语言守卫：把 .java 喂给 ruff 只会得到解析错误（还会被误读成"代码有语法问题"），
        # 所以这里先按文件语言判断，再给出"该用哪个分析器"的明确指引。
        detected = language_for_path(path)
        if detected is not None and not tool_obj.supports(detected):
            supported_for_file = ", ".join(
                sorted(
                    name for name, item in by_name.items() if item.supports(detected)
                )
            )
            hint = (
                f"，该语言的可用分析器：{supported_for_file}"
                if supported_for_file
                else f"，当前没有可用于 {language_spec(detected).display} 的分析器"
            )
            return (
                f"ERROR: 分析器 {analyzer_name} 不支持 "
                f"{language_spec(detected).display} 文件{hint}"
            )

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

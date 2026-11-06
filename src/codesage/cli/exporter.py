"""报告导出：Markdown / JSON。"""

from __future__ import annotations

import json
from pathlib import Path

from codesage.core.languages import LANGUAGE_SPECS
from codesage.core.models import ReviewReport

_SEV_BADGE = {
    "critical": "🔴 P0",
    "major": "🟠 P1",
    "minor": "🟡 P2",
    "info": "🔵 P3",
}


def markdown_fence(language_code: str) -> str:
    """代码围栏名（```java / ```python）。

    围栏不对会让 Markdown 渲染器按错误语法高亮，PR 评论里非常显眼。
    未注册语言退回 text（不猜）。
    """
    for spec in LANGUAGE_SPECS.values():
        if spec.code == language_code:
            return spec.markdown_fence
    return "text"


def to_markdown(report: ReviewReport) -> str:
    """导出 Markdown 报告（可粘到 PR 评论 / 文档）。"""
    lines: list[str] = []
    lines.append(f"# CodeSage 审查报告：{report.target}")
    lines.append("")
    lines.append(f"**评分：{report.score} / 100**")
    lines.append("")

    sev = report.by_severity()
    usage_line = f"文件 {report.files_scanned} 个 · 耗时 {report.duration_sec:.1f}s"
    if report.usage.calls:
        cost = report.metadata.get("cost_yuan", 0.0)
        usage_line += (
            f" · Token {report.usage.total:,}（¥{cost:.3f}）"
            f" · 轮次 {report.turns}"
        )
    lines.append(usage_line)
    lines.append("")
    lines.append(
        f"| P0 | P1 | P2 | P3 |\n|---|---|---|---|\n"
        f"| {sev['critical']} | {sev['major']} | {sev['minor']} | {sev['info']} |"
    )
    lines.append("")

    findings = report.sorted_findings()
    if not findings:
        lines.append("> 未发现问题。")
        return "\n".join(lines)

    for f in findings:
        badge = _SEV_BADGE.get(f.severity.value, f.severity.value)
        location = f"{f.file}:{f.line_start}"
        if f.line_end != f.line_start:
            location += f"-{f.line_end}"
        lines.append(f"## {badge} {f.title}")
        lines.append("")
        lines.append(f"- **位置**：`{location}`（类别：{f.category.zh}）")
        lines.append(
            f"- **来源**：{f.source.zh}"
            + (f" · {f.rule_id}" if f.rule_id else "")
            + f" · 置信度 {f.confidence:.0%}"
        )
        if f.description:
            lines.append("")
            lines.append(f.description)
        if f.evidence:
            lines.append("")
            lines.append(f"```{markdown_fence(f.language_code)}\n{f.evidence}\n```")
        if f.suggestion:
            lines.append("")
            lines.append(f"> **建议**：{f.suggestion}")
        lines.append("")

    return "\n".join(lines)


def to_json(report: ReviewReport) -> str:
    """导出 JSON（供 CI / 工具消费）。"""
    return json.dumps(report.to_dict(), ensure_ascii=False, indent=2)


def export_report(report: ReviewReport, output: Path, fmt: str) -> None:
    """按格式导出到文件。"""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "json":
        output.write_text(to_json(report), encoding="utf-8")
    else:
        output.write_text(to_markdown(report), encoding="utf-8")

"""Rich 终端渲染：把报告变成让人愿意看的输出。

渲染原则：
- 概览先行（分数 / 统计 / 成本一目了然）；
- 按严重级分组，P0/P1 完整展开（说明 + 建议），P2/P3 紧凑列出；
- 来源与置信度透明（用户知道每条是"规则报的"还是"LLM 确认的"）。
"""

from __future__ import annotations

from rich.console import Console, Group
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from codesage.core.models import Finding, ReviewReport, Severity

_SEVERITY_STYLE = {
    Severity.CRITICAL: "bold red",
    Severity.MAJOR: "red",
    Severity.MINOR: "yellow",
    Severity.INFO: "cyan dim",
}

_SEVERITY_ORDER = [Severity.CRITICAL, Severity.MAJOR, Severity.MINOR, Severity.INFO]


def _score_style(score: int) -> str:
    if score >= 90:
        return "bold green"
    if score >= 75:
        return "bold yellow"
    if score >= 60:
        return "bold dark_orange"
    return "bold red"


def render_report(
    report: ReviewReport,
    console: Console,
    *,
    min_severity: Severity = Severity.INFO,
) -> None:
    """渲染完整报告。"""
    render_summary(report, console)
    findings = [
        f
        for f in report.sorted_findings()
        if f.severity.weight >= min_severity.weight
    ]
    if not findings:
        console.print("\n[green]没有达到展示门槛的问题。[/green]")
        return

    console.print()
    for severity in _SEVERITY_ORDER:
        group = [f for f in findings if f.severity == severity]
        if not group:
            continue
        _render_group(severity, group, console)


def render_summary(report: ReviewReport, console: Console) -> None:
    """概览面板：分数 + 统计 + 成本。"""
    sev = report.by_severity()
    cat = report.by_category()

    score_line = Text.assemble(
        ("评分 ", "dim"),
        (f"{report.score}", _score_style(report.score)),
        (" / 100", "dim"),
    )

    stats = Table.grid(padding=(0, 2))
    stats.add_column(style="dim")
    stats.add_column()
    stats.add_row(
        "严重级",
        Text.assemble(
            (f"P0 {sev['critical']}   ", "bold red" if sev["critical"] else "dim"),
            (f"P1 {sev['major']}   ", "red" if sev["major"] else "dim"),
            (f"P2 {sev['minor']}   ", "yellow" if sev["minor"] else "dim"),
            (f"P3 {sev['info']}", "cyan" if sev["info"] else "dim"),
        ),
    )
    stats.add_row(
        "类别",
        Text(
            " | ".join(f"{k} {v}" for k, v in cat.items() if v) or "-",
            style="dim",
        ),
    )

    usage = report.usage
    mode = "LLM + 确定性分析" if report.llm_enabled else "仅确定性分析（零 Token）"
    cost = report.metadata.get("cost_yuan", 0.0)
    extras = [
        f"文件 {report.files_scanned} 个",
        f"耗时 {report.duration_sec:.1f}s",
        f"模式 {mode}",
    ]
    if usage.calls:
        extras.append(
            f"Token {usage.total:,}（¥{cost:.3f}）"
        )
        extras.append(f"轮次 {report.turns}")
        if report.compress_events:
            extras.append(f"压缩 {report.compress_events} 次")
    if report.stop_reason:
        extras.append(f"结束：{report.stop_reason}")

    body = Group(
        score_line,
        Text(""),
        stats,
        Text(""),
        Text("  ·  ".join(extras), style="dim"),
    )
    console.print(
        Panel(
            body,
            title="[bold]CodeSage 审查报告[/bold]",
            subtitle=f"[dim]{report.target}[/dim]",
            border_style="blue",
            padding=(1, 2),
        )
    )


def _render_group(severity: Severity, findings: list[Finding], console: Console) -> None:
    """渲染一个严重级分组。"""
    style = _SEVERITY_STYLE[severity]
    console.print(
        f"[{style}]━━ {severity.label} · {severity.zh}（{len(findings)}）━━[/{style}]"
    )
    for idx, f in enumerate(findings, start=1):
        header = Text.assemble(
            ("#", "dim"),
            (f"{f.id} ", "dim"),
            (f"[{f.severity.label}]", style),
            (f"[{f.category.zh}] ", ""),
            (f"{f.file}:{f.line_start}", "underline"),
            (f"-{f.line_end}" if f.line_end != f.line_start else "", "underline"),
        )
        console.print(f"\n {header}")
        console.print(f"   {escape(f.title)}", style="bold")
        if f.description:
            console.print(f"   [dim]{escape(f.description)}[/dim]")
        if f.suggestion:
            # 多行建议（通常包含修复代码）按行缩进展开；
            # escape 保护代码里的方括号不被 Rich 当标记解析
            lines = f.suggestion.splitlines() or [f.suggestion]
            console.print(f"   [green]→ {escape(lines[0])}[/green]")
            for line in lines[1:]:
                if line.strip():
                    console.print(f"     [green]{escape(line)}[/green]")
                else:
                    console.print()
        source = f.source.zh
        console.print(
            f"   [dim]来源：{source}"
            + (f" · {f.rule_id}" if f.rule_id else "")
            + f" · 置信度 {f.confidence:.0%}[/dim]"
        )
        if severity in (Severity.CRITICAL, Severity.MAJOR) and f.evidence:
            console.print(f"   [dim]代码：[/dim][bright_black]{escape(f.evidence)}[/bright_black]")


def render_skipped(report: ReviewReport, console: Console) -> None:
    """展示被跳过的文件（可解释性）。"""
    skipped = report.metadata.get("scan_skipped") or []
    if not skipped:
        return
    console.print(f"\n[dim]已跳过 {report.files_skipped} 个文件，例如：[/dim]")
    for item in skipped[:5]:
        console.print(f"  [dim]- {item['file']}（{item['reason']}）[/dim]")

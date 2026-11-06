"""review 命令：审查文件或目录（项目的主入口）。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from codesage.cli.exporter import export_report, to_json, to_markdown
from codesage.cli.render import render_report, render_skipped
from codesage.core.config import load_settings
from codesage.core.languages import parse_language_mode
from codesage.core.models import ReviewReport, Severity
from codesage.pipeline import ReviewOptions, run_review

console = Console()

_SEVERITY_FROM_NAME: dict[str, Severity] = {
    "critical": Severity.CRITICAL,
    "major": Severity.MAJOR,
    "minor": Severity.MINOR,
    "info": Severity.INFO,
    # 报告标签别名（P0-P3 与报告展示保持一致，消除两套指代体系的认知摩擦）
    "p0": Severity.CRITICAL,
    "p1": Severity.MAJOR,
    "p2": Severity.MINOR,
    "p3": Severity.INFO,
}

_SEVERITY_HINT = "可用值：critical(P0) / major(P1) / minor(P2) / info(P3)"


def _parse_severity(value: str) -> Severity | None:
    """解析严重级参数（大小写不敏感；名称与 P0-P3 标签均可）。"""
    return _SEVERITY_FROM_NAME.get(str(value).strip().lower())


def _resolve_output_path(output: Path, report: ReviewReport, fmt: str) -> Path:
    """解析导出路径：--output 给目录时自动生成文件名。

    用户直觉是用 `-o <目录>` 表示"导出到这个目录"（类似 cp 的用法），
    此时按 `<目标名>.codesage.<ext>` 落名——避免把目录当文件写入
    导致 PermissionError（真实用户反馈的案例）。
    """
    if not output.is_dir():
        return output
    ext = "json" if fmt == "json" else "md"
    stem = Path(report.target).stem or "codesage_report"
    return output / f"{stem}.codesage.{ext}"


def _make_progress_printer():
    """构造进度事件打印器（stderr 风格的精简进度）。"""

    def on_event(event: str, payload: dict) -> None:
        if event == "scan":
            console.print(
                f"[dim]› 扫描完成：{payload['files']} 个文件"
                f"（跳过 {payload['skipped']}）[/dim]"
            )
        elif event == "static_done":
            analyzers = "/".join(payload.get("analyzers") or []) or "-"
            console.print(
                f"[dim]› 确定性分析（{analyzers}）："
                f"{payload['findings']} 条候选发现[/dim]"
            )
        elif event == "tool_calls":
            console.print(
                f"[dim]› 第 {payload['turn']} 轮工具调用："
                f"{', '.join(payload['names'])}[/dim]"
            )
        elif event == "compress":
            console.print(
                f"[yellow]› 记忆压缩触发（移除 {payload['removed']} 条消息，"
                f"保留 {payload['kept']} 条）[/yellow]"
            )
        elif event == "changed_fallback":
            console.print(f"[yellow]› {payload['reason']}[/yellow]")
        elif event == "language_degraded":
            for language, hint in (payload or {}).items():
                console.print(f"[yellow]› {language}：{hint}[/yellow]")
        elif event == "context" and payload.get("state") in ("hard", "abort"):
            console.print(
                f"[yellow]› 上下文 {payload['tokens']} tokens"
                f"（{payload['ratio']:.0%}）[/yellow]"
            )

    return on_event


def review(
    path: str = typer.Argument(".", help="要审查的文件或目录"),
    profile: str = typer.Option(
        "full", "--profile", "-p", help="审查角色：full / security / performance"
    ),
    fmt: str = typer.Option(
        "terminal", "--format", "-f", help="输出格式：terminal / markdown / json"
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="导出文件路径（配合 --format 使用）"
    ),
    no_llm: bool = typer.Option(
        False, "--no-llm", help="只跑确定性分析（零 Token，无需 API Key）"
    ),
    changed: bool = typer.Option(
        False, "--changed", help="只审查 git 变更涉及的文件（增量模式，对比 HEAD）"
    ),
    language: Optional[str] = typer.Option(
        None,
        "--language",
        "-L",
        help="审查语言范围：auto（默认，按目录实际存在）/ python / java / python,java",
    ),
    max_turns: Optional[int] = typer.Option(
        None, "--max-turns", help="覆盖 LLM 循环的最大轮次（默认取配置值）"
    ),
    min_severity: str = typer.Option(
        "info",
        "--min-severity",
        help="终端展示门槛：只显示该级及以上的问题\n"
        "可选值：critical(P0)/major(P1)/minor(P2)/info(P3)",
    ),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="安静模式（不打印进度）"),
    fail_on: str = typer.Option(
        "none",
        "--fail-on",
        help="CI 退出码阈值：存在该级问题则退出码 1\n"
        "可选值：none/critical(P0)/major(P1)/minor(P2)/info(P3)",
    ),
) -> None:
    """审查指定路径的代码（确定性分析 + 可选 LLM）。

    示例:
      codesage review src/                 # 完整审查（LLM + 确定性分析）
      codesage review src/ --no-llm        # 零成本模式（无需 API Key）
      codesage review . --changed          # 增量：只审 git 变更涉及的文件
      codesage review . --min-severity P1  # 只看 P1 及以上（major 的别名）
      codesage review a.py --format json -o report.json    # 导出 JSON 报告
      codesage review . --fail-on major    # CI：有重要问题则退出码 1
      codesage review . --language java    # 只审 Java（默认 auto：有 .java 就审）
    """
    threshold = _parse_severity(min_severity)
    if threshold is None:
        console.print(
            f"[red]非法 --min-severity：{min_severity}[/red]\n"
            f"[dim]{_SEVERITY_HINT}[/dim]"
        )
        raise typer.Exit(2)
    fail_level = None
    if fail_on.strip().lower() != "none":
        fail_level = _parse_severity(fail_on)
        if fail_level is None:
            console.print(
                f"[red]非法 --fail-on：{fail_on}[/red]\n"
                "[dim]可用值：none / critical(P0) / major(P1) / minor(P2) / info(P3)[/dim]"
            )
            raise typer.Exit(2)

    settings = load_settings()

    if language is not None:
        try:
            parse_language_mode(language)
        except ValueError as exc:
            console.print(f"[red]非法 --language：{exc}[/red]")
            raise typer.Exit(2) from exc

    if not no_llm and not settings.llm_configured and not quiet:
        console.print(
            "[yellow]未检测到 API Key，将降级为确定性分析。[/yellow]"
            "[dim]（配置 .env 后可启用 LLM 深度审查）[/dim]"
        )

    options = ReviewOptions(
        use_llm=not no_llm,
        profile=profile,
        changed_only=changed,
        quiet=quiet,
        max_turns=max_turns,
        languages=language,
        on_event=None if quiet else _make_progress_printer(),
    )

    try:
        report = run_review(path, settings, options)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]错误：{exc}[/red]")
        raise typer.Exit(2) from exc

    # 降级必须显式告知：确定性层缺失 + 没用 LLM 时，这些语言等于"没被检查"，
    # 而报告会显示"无问题"——这是比误报更危险的静默失效。
    degraded = report.metadata.get("language_degraded") or {}
    if degraded and no_llm and not quiet:
        console.print(
            "[bold yellow]注意：以下语言的确定性分析不可用，且本次未启用 LLM，"
            "相关文件实际上未被检查。[/bold yellow]"
        )
        for language_name, hint in degraded.items():
            console.print(f"[yellow]  · {language_name}：{hint}[/yellow]")

    # ---- 输出
    if fmt == "terminal":
        render_report(report, console, min_severity=threshold)
        render_skipped(report, console)
    elif fmt in ("markdown", "json"):
        text = to_markdown(report) if fmt == "markdown" else to_json(report)
        if output is not None:
            target_path = _resolve_output_path(output, report, fmt)
            try:
                export_report(report, target_path, fmt)
            except OSError as exc:
                console.print(f"[red]导出失败：{exc}[/red]")
                console.print(
                    "[dim]提示：--output 应为文件路径（如 report.md）"
                    "或已存在的目录（将自动按 <目标名>.codesage.md 落名）[/dim]"
                )
                raise typer.Exit(2) from exc
            console.print(f"[green]已导出 {fmt} 报告：{target_path}[/green]")
        else:
            # 关闭 Rich 的 markup 解析与自动折行——否则 JSON/Markdown 结构会被破坏
            # （折行会在字符串内引入换行导致 JSON 非法；[xxx] 会被当作标记吞掉）
            console.print(text, markup=False, highlight=False, soft_wrap=True)
    else:
        console.print(f"[red]非法 --format：{fmt}[/red]")
        raise typer.Exit(2)

    # ---- CI 退出码
    if fail_level is not None and any(
        f.severity.weight >= fail_level.weight for f in report.findings
    ):
        raise typer.Exit(1)

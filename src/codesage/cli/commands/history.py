"""history 命令：查看历史审查记录（全局存储，默认展示全部项目）。

支持下钻：`codesage history --detail <ID>` 查看某次审查的完整发现。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from codesage.cli.render import render_report
from codesage.core.models import ReviewReport
from codesage.memory.store import Store, default_store_path
from codesage.optim.feedback import verdict_label

console = Console()


def _display_target(target: str) -> str:
    """目标路径的相对化展示（在当前目录下时显示相对路径，更易读）。"""
    try:
        return Path(target).resolve().relative_to(Path.cwd().resolve()).as_posix()
    except (ValueError, OSError):
        return target


def _normalize_project(project: Path) -> str:
    """归一化 --project 参数，与审查写入端的 project 语义保持一致。

    写入端（pipeline）的 project = 审查根目录：单文件审查时取文件所在目录，
    因此查询端传文件路径时必须同样归一到父目录，否则精确匹配永远失败
    （真实用户反馈：`history -p test.py` 报"暂无记录"，但记录其实在库里）。
    判定规则：存在的文件 → 父目录；带后缀的不存在路径（文件已删除）→ 父目录；
    其余（目录）→ 原样。
    """
    resolved = Path(project).resolve()
    if resolved.is_file() or (not resolved.is_dir() and resolved.suffix):
        return str(resolved.parent)
    return str(resolved)


def history(
    project: Optional[Path] = typer.Option(
        None, "--project", "-p", help="只看某个项目的记录（项目目录路径）"
    ),
    detail: Optional[int] = typer.Option(
        None, "--detail", "-d", help="查看某次审查的完整发现（传记录 ID）"
    ),
    limit: int = typer.Option(10, "--limit", "-n", help="显示最近 N 条记录"),
    show_feedback: bool = typer.Option(
        False, "--feedback", help="显示反馈统计"
    ),
) -> None:
    """查看历史审查记录与详情下钻（存储在 ~/.codesage/store.db）。

    示例:
      codesage history                 # 最近的审查记录
      codesage history --detail 25     # 查看 #25 次审查的具体发现
      codesage history --project .     # 只看当前项目的记录
      codesage history --feedback      # 附反馈统计
    """
    db_path = default_store_path()
    if not db_path.exists():
        console.print(f"[yellow]暂无历史记录（{db_path} 不存在）[/yellow]")
        console.print("[dim]先运行一次 `codesage review <path>` 生成记录。[/dim]")
        raise typer.Exit(0)

    store = Store(db_path)
    try:
        # ---- 详情模式：展示某次审查的完整发现
        if detail is not None:
            payload = store.get_review_payload(detail)
            if payload is None:
                console.print(f"[red]找不到记录 #{detail}[/red]")
                raise typer.Exit(2)
            report = ReviewReport.from_dict(payload)
            console.print(f"[dim]历史报告 #{detail}（重新渲染）[/dim]")
            render_report(report, console)
            return

        # ---- 列表模式
        project_key = _normalize_project(project) if project else None
        rows = store.recent_reviews(limit=limit, project=project_key)
        title = "历史审查记录"
        if project_key:
            title += f"（项目：{project_key}）"
        if not rows:
            console.print("[dim]暂无审查记录。[/dim]")
            raise typer.Exit(0)

        table = Table(title=title, border_style="blue")
        table.add_column("ID", justify="right", style="dim")
        table.add_column("目标")
        table.add_column("评分", justify="right")
        table.add_column("发现", justify="right")
        table.add_column("Token", justify="right")
        table.add_column("耗时", justify="right")
        table.add_column("时间", style="dim")

        for row in rows:
            score = int(row["score"] or 0)
            style = "green" if score >= 90 else ("yellow" if score >= 75 else "red")
            table.add_row(
                str(row["id"]),
                _display_target(str(row["target"])),
                f"[{style}]{score}[/{style}]",
                str(row["findings_count"]),
                f"{int(row['tokens'] or 0):,}",
                f"{float(row['duration_sec'] or 0):.1f}s",
                str(row["created_at"]),
            )
        console.print(table)
        console.print(
            "[dim]提示：`codesage history --detail <ID>` 查看某次审查的具体问题。[/dim]"
        )

        if show_feedback:
            stats = store.feedback_stats(project=project_key)
            if stats:
                console.print("\n[bold]反馈统计[/bold]")
                for verdict, count in sorted(stats.items()):
                    console.print(f"  {verdict_label(verdict)}: {count}")
            else:
                console.print("\n[dim]暂无反馈记录。[/dim]")
    finally:
        store.close()

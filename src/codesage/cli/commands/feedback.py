"""feedback 命令：反馈发现质量 + 错题本管理（自优化闭环的入口）。

存储说明：使用全局存储（~/.codesage/store.db），无需指定项目目录；
反馈的 project 字段自动从对应审查记录反查（错题本按项目隔离）。
"""

from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from codesage.memory.store import Store, default_store_path
from codesage.optim.feedback import FeedbackEntry, record_feedback, verdict_label
from codesage.optim.lessons import LessonBook

console = Console()


def _locate_finding(store: Store, finding_id: str) -> tuple[dict | None, str]:
    """在最近 50 次审查中定位一条发现，同时返回它所属的项目。"""
    for row in store.recent_reviews(limit=50):
        for finding in store.get_review_findings(int(row["id"])):
            if str(finding.get("id")) == finding_id:
                return finding, str(row.get("project") or "")
    return None, ""


def _short_project(project: str) -> str:
    """项目路径的短名展示（最后一段目录名）。"""
    if not project:
        return "（通用）"
    from pathlib import Path

    return Path(project).name or project


def feedback(
    finding_id: Optional[str] = typer.Argument(
        None, help="发现 ID（报告中的 #xxxxxxxx）"
    ),
    verdict: str = typer.Option(
        "wrong", "--verdict", "-v", help="wrong（误报）/ right（确认）/ missed（漏报）"
    ),
    note: str = typer.Option("", "--note", "-N", help="备注（说明原因，会被提炼进错题本）"),
    mine: bool = typer.Option(False, "--mine", help="从反馈中提炼错题本经验"),
    show: bool = typer.Option(False, "--show", help="查看当前错题本内容"),
) -> None:
    """反馈发现质量，维护错题本（自优化闭环：误报/漏报 → 经验 → Prompt 注入）。

    示例:
      codesage feedback 0f9a2657 -v wrong -n "该场景下可忽略"   # 标记误报
      codesage feedback 0f9a2657 -v missed -n "应报未报"        # 标记漏报
      codesage feedback --mine     # 把反馈提炼为错题本经验
      codesage feedback --show     # 查看错题本
    """
    db_path = default_store_path()
    if not db_path.exists() and not mine:
        console.print(f"[yellow]暂无存储：{db_path}[/yellow]（先运行一次 review）")
        raise typer.Exit(2)

    store = Store(db_path)
    book = LessonBook(store)
    try:
        # ---- 查看错题本
        if show:
            lessons = book.all(limit=20)
            if not lessons:
                console.print("[dim]错题本为空。用 `codesage feedback <id> -v wrong` 积累反馈。[/dim]")
                return
            table = Table(title="错题本（经验按项目隔离）", border_style="blue")
            table.add_column("ID", style="dim", justify="right")
            table.add_column("类型")
            table.add_column("项目", style="dim")
            table.add_column("内容")
            rows = store.lessons(limit=20)
            for row in rows:
                kind_label = (
                    "[red]误报[/red]"
                    if row["kind"] == "false_positive"
                    else "[yellow]漏报[/yellow]"
                )
                table.add_row(
                    str(row["id"]),
                    kind_label,
                    _short_project(str(row.get("project") or "")),
                    str(row["content"]),
                )
            console.print(table)
            return

        # ---- 提炼错题本
        if mine:
            added = book.mine_from_feedback()
            console.print(
                f"[green]错题本已更新：新增误报经验 {added['false_positive']} 条、"
                f"漏报经验 {added['false_negative']} 条。[/green]"
            )
            console.print(
                "[dim]下次审查时这些经验会自动注入 system prompt"
                "（按项目隔离，数量有上限）。[/dim]"
            )
            return

        # ---- 记录反馈
        if not finding_id:
            console.print("[red]请提供发现 ID，或使用 --mine / --show[/red]")
            raise typer.Exit(2)

        located, project = _locate_finding(store, finding_id)
        if located is None:
            console.print(
                f"[yellow]在历史记录中未找到 #{finding_id}（仍会记录反馈，"
                "但无法自动填充上下文）。[/yellow]"
            )
            located = {}

        rid = record_feedback(
            store,
            FeedbackEntry(
                finding_id=finding_id,
                verdict=verdict,
                project=project,
                category=str(located.get("category", "")),
                file=str(located.get("file", "")),
                title=str(located.get("title", "")),
                note=note,
            ),
        )
        project_note = f"（项目：{_short_project(project)}）" if project else ""
        console.print(
            f"[green]已记录反馈 #{rid}：{finding_id} → "
            f"{verdict_label(verdict)}{project_note}[/green]"
        )
        console.print(
            "[dim]提示：运行 `codesage feedback --mine` 把反馈提炼为错题本经验。[/dim]"
        )
    finally:
        store.close()

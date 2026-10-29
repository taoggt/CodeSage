"""eval 命令：运行 Harness 评测 / A-B 对比。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from codesage.core.config import load_settings
from codesage.harness.compare import compare
from codesage.harness.runner import EvalOptions, EvalRunner

console = Console()


def eval_command(
    dataset: Path = typer.Option(
        Path("examples/eval_dataset"), "--dataset", "-d", help="评测数据集目录"
    ),
    tag: str = typer.Option("baseline", "--tag", "-t", help="本次评测标签"),
    mock: bool = typer.Option(
        False, "--mock", help="mock 模式：只跑确定性层（无需 API Key，适合 CI）"
    ),
    sample: Optional[int] = typer.Option(
        None, "--sample", "-n", help="只跑前 N 个案例（开发期快速迭代）"
    ),
    compare_tags: Optional[str] = typer.Option(
        None, "--compare", help="A/B 对比模式：传两次评测标签（tagA,tagB），返回对比报告"
    ),
    results_dir: Path = typer.Option(
        Path(".codesage-eval"), "--results-dir", help="评测结果存放目录"
    ),
) -> None:
    """运行 Harness 评测：检出率 / 误报率 / 成本 / A-B 对比。

    示例:
      codesage eval --mock --tag baseline       # 确定性层基线（零成本，无需 Key）
      codesage eval --tag v1-llm                # 完整链路评测（需 API Key）
      codesage eval --sample 3 --tag quick      # 小样本快速迭代
      codesage eval --compare baseline,v1-llm   # A/B 对比两次评测
    """
    # ---- A/B 对比模式
    if compare_tags:
        parts = [p.strip() for p in compare_tags.split(",") if p.strip()]
        if len(parts) != 2:
            console.print("[red]--compare 需要两个标签：tagA,tagB[/red]")
            raise typer.Exit(2)
        result = compare(results_dir, parts[0], parts[1])
        _render_compare(result)
        return

    # ---- 评测模式
    settings = load_settings()
    if not mock and not settings.llm_configured:
        console.print(
            "[yellow]未配置 API Key。可用 --mock 跑确定性层评测，"
            "或配置 .env 后运行完整评测。[/yellow]"
        )
        raise typer.Exit(2)

    def on_progress(case_id: str, idx: int, total: int, result: dict) -> None:
        mark = "[green]检出[/green]" if result["detected"] else "[red]漏报[/red]"
        console.print(
            f"[dim][{idx}/{total}][/dim] {case_id}（{result['category']}）"
            f" → {mark}，报告 {result['reported']} 条"
        )

    runner = EvalRunner(
        settings,
        dataset,
        results_dir,
        EvalOptions(mock=mock, sample=sample),
        on_progress=on_progress,
    )
    summary = runner.run(tag)

    table = Table(title=f"评测结果：{tag}", border_style="blue")
    table.add_column("指标")
    table.add_column("值", justify="right")
    table.add_row("案例数", str(summary["cases"]))
    table.add_row("检出数", str(summary["detected"]))
    table.add_row("检出率 Recall", f"{summary['recall']:.1%}")
    table.add_row("精确率 Precision（近似）", f"{summary['precision']:.1%}")
    table.add_row("F1", f"{summary['f1']:.3f}")
    table.add_row("Token 总量", f"{summary['total_tokens']:,}")
    table.add_row("总成本", f"¥{summary['total_cost_yuan']:.4f}")
    table.add_row("单案例平均成本", f"¥{summary['avg_cost_yuan']:.4f}")
    table.add_row("总耗时", f"{summary['duration_sec']:.1f}s")
    console.print(table)
    console.print(
        f"[dim]详细结果已保存：{results_dir}/{tag}.jsonl、"
        f"{results_dir}/{tag}_summary.json[/dim]"
    )


def _render_compare(result: dict) -> None:
    """渲染 A/B 对比。"""
    table = Table(
        title=f"A/B 对比：{result['tag_a']} → {result['tag_b']}",
        border_style="blue",
    )
    table.add_column("指标")
    table.add_column(result["tag_a"], justify="right")
    table.add_column(result["tag_b"], justify="right")
    table.add_column("变化", justify="right")

    for row in result["metrics"]:
        delta = row.get("delta")
        if delta is None:
            delta_text = "-"
        elif delta > 0:
            delta_text = f"[green]+{delta}[/green]"
        elif delta < 0:
            delta_text = f"[red]{delta}[/red]"
        else:
            delta_text = "0"
        table.add_row(str(row["metric"]), str(row["a"]), str(row["b"]), delta_text)
    console.print(table)

    if result["regressions"]:
        console.print("[red]回归案例（从检出变漏报）：[/red]")
        for item in result["regressions"]:
            console.print(f"  - {item['case_id']}")
    if result["improvements"]:
        console.print("[green]改善案例（从漏报变检出）：[/green]")
        for item in result["improvements"]:
            console.print(f"  + {item['case_id']}")
    if not result["regressions"] and not result["improvements"]:
        console.print("[dim]逐案例结果无变化。[/dim]")

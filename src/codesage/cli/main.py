"""codesage CLI 入口。

命令：
    codesage review <path>    审查代码
    codesage eval             运行 Harness 评测
    codesage history          历史记录
    codesage feedback         反馈与错题本
"""

from __future__ import annotations

import typer
from rich.console import Console

from codesage import __version__
from codesage.cli.commands.eval_cmd import eval_command
from codesage.cli.commands.feedback import feedback
from codesage.cli.commands.history import history
from codesage.cli.commands.review import review

app = typer.Typer(
    name="codesage",
    help=(
        "CodeSage —— AI 代码审查 Agent（确定性静态分析 + 自研 ReAct 内核）\n\n"
        "常用命令:\n"
        "  codesage review <path>          审查代码（LLM + 确定性分析）\n"
        "  codesage review <path> --no-llm  零成本确定性分析（无需 API Key）\n"
        "  codesage eval                    评测：检出率 / 误报率 / 成本 / A-B 对比\n"
        "  codesage history                 历史记录与详情下钻\n"
        "  codesage feedback                反馈与错题本（自优化闭环）\n\n"
        "用 `codesage <command> --help` 查看各命令的完整参数与示例。"
    ),
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_show_locals=False,
)

# 注册命令：不显式传 help，让 Typer 使用完整 docstring（含"示例"段）
app.command("review")(review)
app.command("eval")(eval_command)
app.command("history")(history)
app.command("feedback")(feedback)


@app.callback(invoke_without_command=True)
def _root(
    version: bool = typer.Option(False, "--version", "-V", help="显示版本并退出"),
) -> None:
    if version:
        Console().print(f"CodeSage v{__version__}")
        raise typer.Exit()


if __name__ == "__main__":
    app()

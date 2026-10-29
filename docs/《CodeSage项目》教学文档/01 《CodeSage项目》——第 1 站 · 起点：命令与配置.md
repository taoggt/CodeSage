# 《CodeSage项目》——第 1 站 · 起点：命令与配置

> 📍 **主线进度**：**▶ 起点（本篇）** → 扫描 → 确定性分析 → 组装 → 循环 → 工具 → 后勤 → 合并与输出

一场审查从你敲下回车开始。这一站不讲"分析"，只讲**进入管线之前发生的事**：命令怎么被解析（1.1–1.2）、配置怎么被加载（1.3）、总控函数 `run_review()` 长什么样（1.4）、以及贯穿全线的数据模型 `Finding`（1.5）。走完这一站，你会手里拿着完整的"地图和工具"，再进入下一个阶段。

## 1.1 入口：一个 Typer 应用是怎么搭起来的

CLI 入口在 `cli/main.py`，全文不长，值得整体过一遍：

```python
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
```

四个命令各管一段，对应四个模块：

| 命令 | 模块 | 职责 |
|---|---|---|
| `codesage review <path>` | `cli/commands/review.py` | 主入口：审查代码 |
| `codesage eval` | `cli/commands/eval_cmd.py` | 评测（第 09 篇） |
| `codesage history` | `cli/commands/history.py` | 历史记录与下钻（第 10 篇） |
| `codesage feedback` | `cli/commands/feedback.py` | 反馈与错题本（第 10 篇） |

三个值得注意的细节：

**第一，命令注册故意"不传 help"。** 代码注释写得很明确：

```python
# 注册命令：不显式传 help，让 Typer 使用完整 docstring（含"示例"段）
app.command("review")(review)
```

Typer 默认用函数 docstring 的第一行作为命令帮助；如果注册时显式传了 `help=`，docstring 里精心写的"示例"段就不会出现在 `--help` 里了。所以这里注册时什么都不传——你在 `codesage review --help` 里看到的示例，就是函数 docstring 本身。

**第二，`raise typer.Exit()` 是 Typer 的"正常退出"方式。** `--version` 分支打印完版本号后直接 `Exit()`——这是 Typer 约定的退出信号，看到它就知道"这里的退出是设计好的，不是异常"。

**第三，`if __name__ == "__main__": app()` 配合 `__main__.py` 实现了 `python -m codesage`。** 项目还有一个专门的小文件：

```python
"""支持 `python -m codesage` 运行。"""

from codesage.cli.main import app

if __name__ == "__main__":
    app()
```

装好包之后，`codesage` 命令和 `python -m codesage` 两条路都能进同一个 `app`。

> ### 【Python 加餐 1】模块、包与程序入口（Java 对照）
>
> **模块（module）= 一个 `.py` 文件；包（package）= 一个目录**（含 `__init__.py`）。`codesage.cli.main` 这种点号路径对应目录结构 `src/codesage/cli/main.py`——和 Java 的 `com.foo.Bar` 包路径是同一个思路，但 Python 的"包"不需要和物理目录逐层声明类，目录结构本身就是结构。
>
> **`__init__.py` 是包的标记与"门面"。** 它可以是空的，也可以做转导出。看一个真实例子——`tools/__init__.py` 把 7 个工具工厂统一暴露出来：
>
> ```python
> from codesage.tools.dismiss_candidate import make_dismiss_candidate_tool
> from codesage.tools.list_symbols import make_list_symbols_tool
> ...
> __all__ = [
>     "build_default_tools",
>     "make_dismiss_candidate_tool",
>     ...
> ]
> ```
>
> 这样外部只需 `from codesage.tools import build_default_tools`，不用关心它藏在哪个子文件里——相当于 Java 包里的一个汇总类。`__all__` 列表声明"这个模块公开哪些名字"，目前主要起文档作用。
>
> **程序入口与 Java 不同。** Java 从 `public static void main(String[])` 进；Python 脚本默认从上往下执行，`if __name__ == "__main__":` 是"当这个文件被直接运行（而不是被 import）时才执行"的惯用开关。`__main__.py` 则是一个特殊文件名：`python -m codesage` 时 Python 会去找包里的 `__main__.py` 执行它。
>
> **命令行入口的注册在 `pyproject.toml`：**
>
> ```toml
> [project.scripts]
> codesage = "codesage.cli.main:app"
> ```
>
> 意思是"安装后生成一个 `codesage` 可执行命令，执行时调用 `codesage.cli.main` 模块里的 `app` 对象"。等价于 Java 项目里用启动脚本指定 main class，只是这里的"启动类"是一个 Typer 应用对象。

## 1.2 `review` 命令：每个参数逐一拆

主入口函数在 `cli/commands/review.py`。先看它的签名和 docstring（这是用户见到的"界面"）：

```python
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
    """审查指定路径的 Python 代码（确定性分析 + 可选 LLM）。

    示例:
      codesage review src/                 # 完整审查（LLM + 确定性分析）
      codesage review src/ --no-llm        # 零成本模式（无需 API Key）
      codesage review . --changed          # 增量：只审 git 变更涉及的文件
      codesage review . --min-severity P1  # 只看 P1 及以上（major 的别名）
      codesage review a.py --format json -o report.json    # 导出 JSON 报告
      codesage review . --fail-on major    # CI：有重要问题则退出码 1
    """
```

十个参数的全表（这是"参数说明"最完整的一处，值得收藏）：

| 参数 | 类型/默认值 | 含义 | 备注 |
|---|---|---|---|
| `path` | 位置参数，默认 `.` | 要审查的文件或目录 | 唯一的位置参数 |
| `--profile / -p` | str，`full` | 审查角色 | `full` / `security` / `performance` |
| `--format / -f` | str，`terminal` | 输出格式 | `terminal` / `markdown` / `json` |
| `--output / -o` | 路径，无默认 | 导出目标 | 给目录时自动生成文件名（见下） |
| `--no-llm` | 开关，False | 只跑确定性分析 | 零 Token、无需 API Key |
| `--changed` | 开关，False | 只审 git 变更涉及的文件 | 对比 HEAD（第 02 篇细讲） |
| `--max-turns` | int，无默认 | 覆盖循环最大轮次 | 默认取配置 `review.max_turns`（12） |
| `--min-severity` | str，`info` | 终端展示门槛 | 支持 `P0-P3` 别名 |
| `--quiet / -q` | 开关，False | 不打印进度 | 报告本体照常输出 |
| `--fail-on` | str，`none` | CI 退出码阈值 | 支持 `P0-P3` 别名 |

### 严重级参数：两套指代，一个入口

注意 `--min-severity` 和 `--fail-on` 的帮助文本里都写着 `critical(P0)/major(P1)...`——这是有意为之。代码里维护了一张带别名的映射表：

```python
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
```

报告里展示的是 `P0-P3` 标签，代码里叫 `critical/major/minor/info`——两套指代本来就存在。与其让用户在文档和命令行之间做翻译，不如两边都收：

```python
def _parse_severity(value: str) -> Severity | None:
    """解析严重级参数（大小写不敏感；名称与 P0-P3 标签均可）。"""
    return _SEVERITY_FROM_NAME.get(str(value).strip().lower())
```

所以 `--min-severity major`、`--min-severity P1`、`--min-severity p1` 是同一件事。传了非法值则打印提示并**以退出码 2 结束**（退出码约定见后文）。

### `--output` 的一个真实修复：给目录时自动落名

```python
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
```

逻辑很薄但很关键：`-o` 给的是**已存在的目录**（比如 `-o D:\reports`）时，自动生成 `test.codesage.md` 这样的文件名；否则按原样当文件路径处理。注释里那句"真实用户反馈的案例"说明了这个函数为什么存在——旧版本把目录当文件写入，直接 `PermissionError`。

### 进度打印器：事件名 → 输出行

审查过程中管线会发射一串事件（第 1.4 节列全表），非 `--quiet` 模式下由这个函数消费：

```python
def _make_progress_printer():
    """构造进度事件打印器（stderr 风格的精简进度）。"""

    def on_event(event: str, payload: dict) -> None:
        if event == "scan":
            console.print(
                f"[dim]› 扫描完成：{payload['files']} 个文件"
                f"（跳过 {payload['skipped']}）[/dim]"
            )
        ...
```

每个事件对应的实际输出：

| 事件 | 输出示例 |
|---|---|
| `scan` | `› 扫描完成：62 个文件（跳过 8）` |
| `static_done` | `› 确定性分析（ruff/bandit/radon）：133 条候选发现` |
| `tool_calls` | `› 第 3 轮工具调用：read_file, submit_finding` |
| `compress` | `› 记忆压缩触发（移除 6 条消息，保留 8 条）`（黄色） |
| `changed_fallback` | `› 当前目录不是 git 仓库，已回退为全量审查`（黄色） |
| `context`（state 为 hard/abort 时） | `› 上下文 51200 tokens（80%）`（黄色） |

注意 `[dim]` `[yellow]` 这些 Rich 标记——它们不打印给用户看，而是被 Rich 解释成颜色/样式。

### 执行流程与退出码

`review()` 函数体按固定顺序做七件事：

```text
1. 解析并校验 --min-severity / --fail-on（非法 → 退出码 2）
2. load_settings() 加载配置
3. 未配 API Key 且未 --no-llm → 提示"将降级为确定性分析"
4. 组装 ReviewOptions（见 1.4），带上进度打印器
5. run_review(path, settings, options)  ← 主战场，后面七篇全部围绕它
6. 按 --format 输出：终端渲染 / 导出 Markdown / 导出 JSON
7. CI 退出码：存在达到 --fail-on 阈值的问题 → 退出码 1
```

第 7 步的判定只有四行：

```python
    # ---- CI 退出码
    if fail_level is not None and any(
        f.severity.weight >= fail_level.weight for f in report.findings
    ):
        raise typer.Exit(1)
```

这里用严重级的 `weight`（15/8/3/1）做"达到或超过阈值"的比较——第 1.5 节会讲这个 weight 的来历。退出码的完整约定：

| 退出码 | 含义 | 触发场景 |
|---|---|---|
| 0 | 正常 | 审查完成且未达到 `--fail-on` 阈值 |
| 1 | CI 拦截 | 存在达到 `--fail-on` 阈值的问题 |
| 2 | 用法错误 | 参数非法 / 路径不存在 / 无文件可审 / 导出失败 |

`0/1/2` 这套约定是 Unix CLI 的惯例：**0 成功、1 业务性失败、2 用法性失败**——CI 脚本里 `if codesage review . --fail-on major; then` 就能直接拿退出码做门禁。

> ### 【Python 加餐 2】默认参数与关键字参数（Java 对照）
>
> 上面签名里满屏的 `typer.Option("full", "--profile", "-p", help=...)`——先理解 Python 函数参数本身。
>
> **Python 没有重载（overload）。** Java 里写两个同名方法不同参数列表，Python 里不允许同名函数共存。替代方案有两个：
> 1. **默认值**：`def f(a, b=0, c=None)` 一个函数覆盖多种调用形态；
> 2. **关键字参数**：调用时 `f(a, c=3)` 可以跳过中间的 `b`——只要被跳过的是有默认值的参数。这在 Java 里做不到（Java 只能位置传参或构造 Builder）。
>
> **`= value` 的默认值只在函数定义时求值一次**——这是 Python 的头号新手陷阱，本项目专门有一条规则（CG001）在抓它。第 03 篇【Python 加餐 13】会把这个坑彻底讲清楚。这里先记住结论：**永远不要用 `[]`、`{}` 这种可变对象当默认值**。
>
> **Typer 把注解和默认值变成了命令行界面。** `profile: str = typer.Option("full", "--profile", "-p", ...)` 这行的意思是：参数 `profile` 类型是 `str`、命令行里叫 `--profile`（或 `-p`）、不给时默认 `"full"`、帮助文本是那段中文。`typer.Argument(".")` 则声明"这是位置参数"。也就是说 Typer 的"声明式 CLI"本质是：**函数签名即界面**——和 Java 的 argparse 风格（或 Spring 的注解）相比，少了样板代码，多的是"注解驱动"的思想，只是 Python 的注解是运行时真读得到的数据（借助 Typer），不像 Java 注解需要反射框架去扫。

## 1.3 配置：三个来源、三组设置、全部配置项

`settings = load_settings()` 在 `core/config.py`。这个模块的头部注释先交代了优先级：

```python
"""配置加载：环境变量 + codesage.toml + 代码默认值。

优先级：环境变量 > codesage.toml > 默认值。

环境变量命名规则（pydantic-settings 约定）：
    CODESAGE_LLM__API_KEY=xxx      → settings.llm.api_key
    CODESAGE_BUDGET__CONTEXT_WINDOW=64000 → settings.budget.context_window
（双下划线 __ 表示嵌套层级）
"""
```

三层结构的加载顺序（`settings_customise_sources` 逐行）：

```python
        """数据源顺序：显式初始化 > 环境变量 > .env > codesage.toml。"""
        sources: list[PydanticBaseSettingsSource] = [
            init_settings,
            env_settings,
            dotenv_settings,
        ]
        toml_path = Path("codesage.toml")
        if toml_path.exists():
            sources.append(
                TomlConfigSettingsSource(settings_cls, toml_file=str(toml_path))
            )
        return tuple(sources)
```

**排在前面的来源覆盖后面的**。所以实际生效顺序是：

```text
显式传入（代码里直接构造 Settings(...)）  ← 最高
    ↑ 覆盖
环境变量（CODESAGE_LLM__API_KEY=...）
    ↑ 覆盖
.env 文件（项目根目录，git 忽略）
    ↑ 覆盖
codesage.toml（存在才加载——"避免文件不存在时的配置告警"）
    ↑ 覆盖
代码默认值                                ← 最低
```

### 全部配置项（三组，一张表收藏）

**`[llm]` 组——LLM 接入（默认指向 DeepSeek）：**

| 字段 | 类型 | 默认值 | 含义 | 环境变量 |
|---|---|---|---|---|
| `api_key` | str | `""` | API 密钥；空 = 未配置 | `CODESAGE_LLM__API_KEY` |
| `base_url` | str | `https://api.deepseek.com` | OpenAI 兼容服务地址 | `CODESAGE_LLM__BASE_URL` |
| `model_strong` | str | `deepseek-chat` | 深度审查用模型 | `CODESAGE_LLM__MODEL_STRONG` |
| `model_fast` | str | `deepseek-chat` | 压缩摘要用模型 | `CODESAGE_LLM__MODEL_FAST` |
| `temperature` | float | `0.2` | 采样温度（审查场景求稳） | `CODESAGE_LLM__TEMPERATURE` |
| `max_output_tokens` | int | `4096` | 单次响应最大 token | `CODESAGE_LLM__MAX_OUTPUT_TOKENS` |
| `timeout` | float | `120.0` | 请求超时（秒） | `CODESAGE_LLM__TIMEOUT` |

**`[budget]` 组——Token 预算与压缩阈值：**

| 字段 | 类型 | 默认值 | 含义 | 环境变量 |
|---|---|---|---|---|
| `context_window` | int | `64000` | 模型上下文窗口 | `CODESAGE_BUDGET__CONTEXT_WINDOW` |
| `soft_ratio` | float | `0.6` | ≥60%：回合边界触发压缩 | `CODESAGE_BUDGET__SOFT_RATIO` |
| `hard_ratio` | float | `0.8` | ≥80%：调用前强制压缩 | `CODESAGE_BUDGET__HARD_RATIO` |
| `abort_ratio` | float | `0.95` | ≥95%：熔断输出已有发现 | `CODESAGE_BUDGET__ABORT_RATIO` |
| `input_price_per_m` | float | `2.0` | 输入单价（元/百万 token） | `CODESAGE_BUDGET__INPUT_PRICE_PER_M` |
| `output_price_per_m` | float | `8.0` | 输出单价（元/百万 token） | `CODESAGE_BUDGET__OUTPUT_PRICE_PER_M` |

**`[review]` 组——审查行为：**

| 字段 | 类型 | 默认值 | 含义 | 环境变量 |
|---|---|---|---|---|
| `max_turns` | int | `12` | ReAct 循环轮次上限 | `CODESAGE_REVIEW__MAX_TURNS` |
| `active_window_turns` | int | `4` | 记忆活跃区保留轮数 | `CODESAGE_REVIEW__ACTIVE_WINDOW_TURNS` |
| `max_file_lines` | int | `2000` | 大文件预过滤阈值 | `CODESAGE_REVIEW__MAX_FILE_LINES` |
| `max_chunk_lines` | int | `400` | 单块最大行数（切块预留） | `CODESAGE_REVIEW__MAX_CHUNK_LINES` |
| `tool_output_limit` | int | `500` | 工具单次输出行数上限 | `CODESAGE_REVIEW__TOOL_OUTPUT_LIMIT` |
| `max_candidates_in_prompt` | int | `40` | 任务简报注入的候选上限 | `CODESAGE_REVIEW__MAX_CANDIDATES_IN_PROMPT` |
| `store_path` | str | `""` | 存储路径；空 = 用户级默认 | `CODESAGE_REVIEW__STORE_PATH` |
| `ignore_dirs` | list[str] | 见下 | 扫描排除目录 | `CODESAGE_REVIEW__IGNORE_DIRS` |

`ignore_dirs` 的默认值是：

```python
    ignore_dirs: list[str] = Field(
        default_factory=lambda: [
            ".git", "__pycache__", ".venv", "venv", "node_modules",
            ".mypy_cache", ".pytest_cache", ".ruff_cache",
            "build", "dist", ".eggs", ".codesage",
        ]
    )
```

注意这里写的是 `default_factory=lambda: [...]` 而不是 `= [...]`——这正是【Python 加餐 2】里说的"可变默认值陷阱"的**正确写法**：用一个函数在每次构造时生成新列表，所有实例不会共享同一个列表。`Field(default_factory=...)` 是 pydantic 提供的钩子。完整原理在第 03 篇【Python 加餐 13】。

### 配置文件示例

`codesage.toml`（放在运行目录即可生效，模板见 `codesage.toml.example`）：

```toml
[llm]
# API Key 建议放 .env，不要写进这里提交到 git
base_url = "https://api.deepseek.com"
model_strong = "deepseek-chat"     # 深度审查使用的模型
model_fast = "deepseek-chat"       # 工具决策 / 摘要压缩使用的模型
temperature = 0.2
max_output_tokens = 4096
timeout = 120.0

[budget]
context_window = 64000        # 模型上下文窗口（token）
soft_ratio = 0.6              # 使用率 ≥ 60%：回合边界触发压缩
hard_ratio = 0.8              # 使用率 ≥ 80%：调用前强制压缩
abort_ratio = 0.95            # 使用率 ≥ 95%：熔断，输出已有发现
input_price_per_m = 2.0       # 输入单价（元 / 百万 token），用于成本估算
output_price_per_m = 8.0      # 输出单价（元 / 百万 token）

[review]
max_turns = 12                # ReAct 循环最大轮次
active_window_turns = 4       # 记忆活跃区保留的最近轮数
max_file_lines = 2000         # 超过则跳过（大文件预过滤）
max_chunk_lines = 400         # 单块最大行数
tool_output_limit = 500       # 工具单次输出最大行数
max_candidates_in_prompt = 40 # 注入初始上下文的候选发现上限
# store_path = ""             # 历史/错题本存储；留空 = 用户级默认 ~/.codesage/store.db
```

`.env`（更常用的方式，只放敏感信息与机器相关配置）：

```bash
# ---- DeepSeek（推荐：国内直连、成本极低、OpenAI 兼容） ----
CODESAGE_LLM__API_KEY=sk-你的DeepSeek密钥
CODESAGE_LLM__BASE_URL=https://api.deepseek.com

# 模型分级路由：强模型做深度审查，快模型做工具决策与摘要压缩
# 预算紧张时两者都填 deepseek-chat；深度分析场景可将 strong 换成 deepseek-reasoner
CODESAGE_LLM__MODEL_STRONG=deepseek-chat
CODESAGE_LLM__MODEL_FAST=deepseek-chat

# ---- 其他 OpenAI 兼容服务示例（任选其一，改上面两行即可） ----
# 通义千问：  CODESAGE_LLM__BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
# 智谱 GLM：  CODESAGE_LLM__BASE_URL=https://open.bigmodel.cn/api/paas/v4
# OpenAI：    CODESAGE_LLM__BASE_URL=https://api.openai.com/v1
```

### 钥匙兜底：不需要记住 `CODESAGE_` 前缀

最后一道人性化设计——如果你已经习惯导出 `DEEPSEEK_API_KEY`，不配置任何东西也能跑：

```python
# 常见环境变量兜底顺序（用户不必记住 CODESAGE_ 前缀）
_FALLBACK_API_KEY_ENVS = ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "DASHSCOPE_API_KEY")


def load_settings() -> Settings:
    """加载配置并做 API Key 兜底。"""
    settings = Settings()
    if not settings.llm.api_key:
        for env_name in _FALLBACK_API_KEY_ENVS:
            value = os.environ.get(env_name, "").strip()
            if value:
                settings.llm.api_key = value
                break
    return settings
```

`Settings` 上还有一个贯穿全局的属性：

```python
    @property
    def llm_configured(self) -> bool:
        """是否具备调用 LLM 的条件。"""
        return bool(self.llm.api_key.strip())
```

它是"降级模式"的总开关：`review` 命令、`pipeline.run_review`、`eval` 命令三个入口都读它，决定"走完整链路还是确定性层"。**"没配置 key 也能用"不是偶然，是被三处判断有意保住的能力。**

> ### 【Python 加餐 3】类型注解与 pydantic（Java 对照）
>
> **关键差异先说：Python 的类型注解不参与运行，Java 的类型系统参与编译。**
>
> ```python
> def f(x: int) -> str: ...
> ```
>
> 这里的 `int` / `str` 只是**元数据**——Python 运行时不会因为你传了字符串就报错（没有编译器拦你）。那它有什么用？三件事：
> 1. **给人和工具看**：IDE 补全、静态检查器（mypy）、以及本项目里 Typer（把注解变成 CLI 参数类型）和 pydantic（把注解变成配置校验规则）；
> 2. **可自省**：运行时代码可以用 `inspect` 读到注解——注册表生成 JSON Schema 就靠这个（第 04 篇【Python 加餐 17】）；
> 3. **文档**：注解本身就是最好的接口文档。
>
> **pydantic 把"不强制"补成了"强制"。** `BaseModel` 子类的字段声明 `api_key: str = ""` 在**实例化时**会被校验和转换：传 `"123"` 给 int 字段会被转成 123，传错类型会抛 `ValidationError`。这就把 Java 式的"构造时类型安全"在运行层面找回来了——代价是校验发生在运行时而非编译时。
>
> **`Optional[Path] = None` 与 `list[str]`。** 前者等价于 `Path | None`（可空），后者是"字符串列表"的泛型写法——和 Java 的 `Optional<Path>`、`List<String>` 是同一个目的，Python 3.9+ 可以直接用内置类型泛型（不用 `typing.List`）。本项目文件头普遍有 `from __future__ import annotations`，它让注解"延迟求值"（存成字符串），避免旧版本 Python 的兼容问题——你可以把它当成无害的惯例头。

## 1.4 总控 `run_review()`：五阶段全景

配置就绪，`review()` 命令第 5 步把三样东西交给总控：

```python
        report = run_review(path, settings, options)
```

`run_review` 在 `pipeline.py`——**后面七篇全部围绕这个函数展开**。先立一个总印象。它的开头是：

```python
def run_review(
    path: str | Path,
    settings: Settings | None = None,
    options: ReviewOptions | None = None,
) -> ReviewReport:
    """执行一次完整审查，返回报告。"""
    started = time.perf_counter()
    settings = settings or load_settings()
    options = options or ReviewOptions()

    target = Path(path).expanduser().resolve()
    if not target.exists():
        raise FileNotFoundError(f"路径不存在：{target}")

    workspace = target if target.is_dir() else target.parent
```

`settings or load_settings()` 是 Python 里常见的"可选依赖"写法（`or` 短路：传了就用传的，没传现场加载）；`Path(path).expanduser().resolve()` 把 `~` 展开、相对路径转绝对路径——**"工作区"（workspace）的定义**：目标是目录就用它，是单文件就用它所在目录。这个定义在后面到处出现（工具的越权防护、错题本的项目键、报告的相对路径都以 workspace 为基准）。

`ReviewOptions` 是这次审查的"行为开关"，字段如下：

| 字段 | 类型 | 默认值 | 含义 |
|---|---|---|---|
| `use_llm` | bool | `True` | 是否启用 LLM 阶段（`--no-llm` 置 False） |
| `profile` | str | `"full"` | 审查角色 |
| `changed_only` | bool | `False` | 是否只审 git 变更文件 |
| `quiet` | bool | `False` | 是否安静模式 |
| `max_turns` | int \| None | `None` | 覆盖循环轮次上限（None = 用配置值） |
| `on_event` | EventHook \| None | `None` | 事件钩子（CLI 进度打印器） |

然后是五个阶段——文件头注释画的骨架，与代码里的段落注释一一对应：

```text
    扫描（零 Token）
      → 确定性分析（零 Token：AST 规则 + Ruff + Bandit + radon）
      → Agent 循环（Token：复核候选 + 补充发现 + 修复建议）
      → 合并去重（LLM 驳回的候选被过滤）→ 评分 → 落盘
```

| 阶段 | 代码段落 | 做什么 | 输出 | 展开在哪篇 |
|---|---|---|---|---|
| ① 扫描 | `# ----- 1. 扫描` | 筛文件、读源码、建清单 | `files` / `sources` / `file_inventory` | 第 02 篇 |
| ② 确定性分析 | `# ----- 2. 确定性分析` | 13 条规则 + 3 个分析器 | `det_findings`（候选发现） | 第 03 篇 |
| ③ Agent 循环 | `# ----- 3. Agent 循环` | 组装 + 循环 + 复核 | `llm_findings` / `dismissed` / 用量 | 第 04–07 篇 |
| ④ 合并聚合 | `# ----- 4. 合并与聚合` | 驳回过滤、去重、评分 | `ReviewReport` | 第 08 篇 |
| ⑤ 落盘 | `# ----- 5. 落盘` | 写 SQLite、发 `done` 事件 | `review_id` | 第 08 篇 |

**事件流**是五阶段之间的"广播"：`_emit(options, 事件名, 载荷)` 在关键节点被调用，最终流向 `ReviewOptions.on_event`（CLI 的进度打印器）。一次完整审查的事件序列：

```text
start → scan → static_done → [context → tool_calls → tool_result → compress ... 每轮]
      → done
```

（`start` / `context` / `tool_calls` / `tool_result` / `compress` / `end` 由第 05 篇的循环发射；`scan` / `static_done` / `done` 由管线发射；`changed_fallback` 在 `--changed` 且非 git 仓库时发射。）

另外注意 `_emit` 的实现——事件钩子抛异常会被吞掉：

```python
def _emit(options: ReviewOptions, event: str, payload: dict[str, Any]) -> None:
    if options.on_event is not None:
        try:
            options.on_event(event, payload)
        except Exception:  # noqa: BLE001
            pass
```

**观测设施不能反噬主流程**——这是贯穿整个项目的容错哲学之一（第 11 篇汇总全部十处）。

## 1.5 贯穿全线的数据模型：`Finding` 与它的伙伴们

五个阶段之间传递的"问题"只有一种类型：`core/models.py` 的 `Finding`。文件头注释：

```python
"""核心领域模型：Finding / ReviewReport / TokenUsage。

设计约定（解耦的关键）：
- 所有模块之间传递的数据都使用这里的模型：
  分析层（AST 规则 / Ruff / Bandit）与 Agent 层互不认识，
  但产出的都是同一个 Finding，报告层只认 Finding。
- Finding 是唯一的“问题”载体，无论来源是规则还是 LLM。
"""
```

### `Finding`：一条审查发现

| 字段 | 类型 | 默认值 | 含义 |
|---|---|---|---|
| `file` | str | 必填 | 相对工作区根目录的 POSIX 路径 |
| `line_start` / `line_end` | int | 必填 | 1-based 行号范围 |
| `category` | Category | 必填 | 五维度之一（见下） |
| `severity` | Severity | 必填 | 四级严重度（见下） |
| `title` | str | 必填 | 一句话标题 |
| `description` | str | `""` | 问题详细说明与触发条件 |
| `suggestion` | str | `""` | 具体修复建议（可含代码） |
| `evidence` | str | `""` | 证据代码行（截 200 字符） |
| `confidence` | float | `1.0` | 置信度 0~1 |
| `source` | Source | `AST_RULE` | 来源（规则/Ruff/Bandit/radon/LLM） |
| `rule_id` | str | `""` | 规则编号（CG001~CG013 / `CG-LLM`） |

它有两个计算型属性。第一个是短 ID（CLI 里引用发现用，比如 `codesage feedback <id>`）：

```python
    @property
    def id(self) -> str:
        """稳定的短 ID（用于 CLI 反馈与报告引用）。

        使用 SHA1 仅为生成短标识（非安全用途），
        usedforsecurity=False 明确声明这一点（同时消除 Bandit B324 告警）。
        """
        key = f"{self.file}:{self.line_start}:{self.rule_id or self.title}:{self.source.value}"
        return hashlib.sha1(key.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]
```

第二条注释很有意思：**项目用 Bandit 扫别人，也得处理 Bandit 扫自己的告警**。`usedforsecurity=False` 是明确声明"这个 SHA1 只生成短标识、不承担安全职责"——顺带消掉 Bandit 的 B324 告警。

第二个是去重键：

```python
    @property
    def dedup_key(self) -> str:
        """去重键：同文件 + 相近行 + 同类别视为同一问题（保留高置信度者）。"""
        return f"{self.file}:{self.line_start // 3}:{self.category.value}"
```

`line_start // 3` 是"相近行"的整数近似——同一处问题被不同工具报出来时行号常差一两行，除以 3 把它们归到同一格（第 08 篇讲去重怎么用它）。

### 三个枚举：Severity / Category / Source

`Severity`（严重级，报告里显示为 P0–P3）：

| 枚举值 | 标签 | 权重（扣分） | 中文 | 语义 |
|---|---|---|---|---|
| `CRITICAL` | P0 | 15 | 严重 | 安全漏洞 / 必然崩溃 |
| `MAJOR` | P1 | 8 | 重要 | 明确 bug / 严重性能问题 |
| `MINOR` | P2 | 3 | 一般 | 代码质量 / 可维护性 |
| `INFO` | P3 | 1 | 提示 | 风格建议 / 最佳实践 |

`Category`（维度）：`QUALITY`（代码质量）、`BUG`（潜在 Bug）、`PERFORMANCE`（性能）、`SECURITY`（安全）、`BEST_PRACTICE`（最佳实践），各带一个 `zh` 中文属性。

`Source`（来源）：`AST_RULE`、`RUFF`、`BANDIT`、`RADON`、`LLM`——决定报告里的"由谁发现"以及去重时的优先级（第 08 篇）。

### `ReviewReport`：一次审查的产出

| 字段 | 类型 | 默认值 | 含义 |
|---|---|---|---|
| `target` | str | 必填 | 审查目标（绝对路径） |
| `findings` | list[Finding] | `[]` | 合并去重后的全部发现 |
| `duration_sec` | float | 0.0 | 总耗时 |
| `usage` | TokenUsage | 空用量 | Token 统计 |
| `turns` | int | 0 | 循环实际轮次 |
| `llm_enabled` | bool | False | 是否走了 LLM 链路 |
| `stop_reason` | str | `""` | 循环结束原因 |
| `compress_events` | int | 0 | 压缩触发次数 |
| `files_scanned` / `files_skipped` | int | 0 | 扫描统计 |
| `metadata` | dict | `{}` | 成本、候选数、驳回记录等 |

评分算法是它最核心的属性：

```python
    @property
    def score(self) -> int:
        """0-100 评分：100 起步按严重级扣分。"""
        score = 100
        for f in self.findings:
            score -= f.severity.weight
        return max(0, score)
```

它还有一个反序列化入口（新增——`history --detail` 下钻历史报告时用它，第 10 篇）：

```python
    @classmethod
    def from_dict(cls, data: dict) -> ReviewReport:
        """从 to_dict() 的产物反序列化（用于加载历史报告）。"""
```

和 `TokenUsage`（`input_tokens` / `output_tokens` / `calls` 三个计数 + `cost_yuan()` 成本换算）——它们是"上下文经济学"（第 07 篇）的记账本。

> ### 【Python 加餐 4】Enum 枚举（Java 对照）
>
> Java 的 enum 是完整类；**Python 的 Enum 也是类**，但用法更轻：
>
> ```python
> class Severity(str, Enum):
>     CRITICAL = "critical"
>     ...
> ```
>
> 关键在 `(str, Enum)` 这个"双继承"：让成员**同时是字符串**。效果是 `Severity.CRITICAL == "critical"` 为 True，也能直接当字符串用（拼进 f-string、`json.dumps`）。代价是成员不再是"纯净的枚举"——但在这个项目里到处要把枚举拼进报告文本和 JSON，收益远大于代价。Java 要表达类似的东西得写 `enum Severity implements CharSequence` 之类，远没有这么随手。
>
> 取值转换：`Severity("critical")` 从字符串还原枚举成员，传非法值抛 `ValueError`——这就是 `submit_finding` 工具里"枚举校验失败回填 ERROR"的实现（第 06 篇）。
>
> 给枚举挂方法也是普通类手法——用 `@property`：
>
> ```python
>     @property
>     def label(self) -> str:
>         return {"critical": "P0", ...}[self.value]
> ```
>
> 相当于 Java enum 里的实例方法 + switch，只是 Python 用字典查表更省事。

> ### 【Python 加餐 5】dataclass（Java 对照）
>
> `Finding`、`ReviewReport`、`TokenUsage` 全都顶着 `@dataclass`。它的作用是**自动生成样板代码**：
>
> ```python
> @dataclass
> class Finding:
>     file: str
>     line_start: int
>     description: str = ""
> ```
>
> 等价于手写 `__init__`（把注解变成构造参数、带默认值的参数可省略）、`__repr__`（打印调试友好）、`__eq__`（按字段比较）。这正好命中 Java 里两种东西的场景：**POJO + Lombok `@Data`**，或者 Java 16+ 的 `record`（关键是 record 创建后不可变，而 dataclass 默认可变，可用 `frozen=True` 声明不可变）。
>
> 两个项目里实际用到的细节：
>
> ```python
>     findings: list[Finding] = field(default_factory=list)
>     usage: TokenUsage = field(default_factory=TokenUsage)
> ```
>
> `field(default_factory=...)` 又是"可变默认值陷阱"的正确解法——每个实例拿到各自的列表/对象，而不是共享一个（第 03 篇【Python 加餐 13】）。为什么不能直接 `= []`？到时见分晓。
>
> 另外 dataclass 和 pydantic 的 `BaseModel` 是两套东西：前者只生成样板、不做数据校验（快、零依赖）；后者做校验和类型转换（配置用）。项目对"内部数据"用 dataclass，"外部输入"用 pydantic。

> ### 【Python 加餐 6】@property（Java 对照）
>
> Java 里"计算属性"要写 `getScore()`；Python 有两条路：`score()` 方法，或者 `@property` 装饰成"看起来像字段"：
>
> ```python
>     @property
>     def score(self) -> int:
>         ...
> report.score      # 像读字段一样，实际执行了函数
> ```
>
> 好处：调用方少写括号，接口语义更准（"这是个查询，不是计算动作"）；坏处：把"可能较重的计算"藏在一行读操作后面。项目的用法很克制——`score`、`label`、`total`、`dedup_key` 全是**纯计算、便宜、每次结果一致**的属性；真正有副作用的动作一律是带括号的方法。
>
> Java 对照：约等于给字段写了个 getter，但不需要显式调用——Python 用"描述符协议"在属性访问时插入函数调用。顺带一提，`@property` 和后面要讲的 `@dataclass`、`@tool` 都是**装饰器**（decorator）——第 04 篇【Python 加餐 15】会把装饰器的本质讲透，你会发现它和 Java 的注解只有"长得像"这一个共同点。

## 1.6 小结与下一站

- **入口**：Typer 把函数签名变成 CLI；四个命令注册时故意不传 `help`，保留 docstring 里的示例；`--version` 用 `typer.Exit()` 正常退出。
- **参数**：`review` 十个参数全表在手；严重级收 `critical/P0` 两套指代；`--output` 给目录会自动落名（真实反馈修复）；退出码 0/1/2 对应成功/CI 拦截/用法错误。
- **配置**：显式 > 环境变量 > `.env` > `codesage.toml` > 默认值；三组配置全表在手；`DEEPSEEK_API_KEY` 等常见变量兜底；`store_path` 留空时用全局 `~/.codesage/store.db`（第 08 篇细讲）。
- **总控**：`run_review` 五个阶段 + 一条事件流；`workspace` 的定义（目录本身或文件的父目录）从这一幕开始贯穿全剧。
- **数据模型**：`Finding` 是唯一的问题载体；`Severity.weight` 决定扣分；`ReviewReport.score = 100 − Σweight`；`id`/`dedup_key` 两个计算属性有明确的用途（反馈引用 / 去重）。
- **Python 加餐**：模块与包、默认参数、类型注解与 pydantic、Enum、dataclass、@property——六个专题的代码全部来自本项目。

下一站进入**阶段一 · 扫描**：带着 `settings`（三组配置）和 `options`（行为开关），`run_review` 要做的第一件事是"决定审哪些文件"。那里的第一个函数调用是 `FileFilter(workspace, excludes=..., max_file_lines=...)`。

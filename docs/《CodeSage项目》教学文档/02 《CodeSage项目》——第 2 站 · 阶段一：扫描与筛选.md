# 《CodeSage项目》——第 2 站 · 阶段一：扫描与筛选

> 📍 **主线进度**：起点 ✅ → **▶ 扫描（本篇）** → 确定性分析 → 组装 → 循环 → 工具 → 后勤 → 合并与输出

`run_review()` 拿到 `settings` 和 `options` 后，第一件事是回答一个问题：**这次审查到底审哪些文件？** 这就是阶段一——扫描。它的产物将被后面所有阶段使用，所以这一篇要把"哪些文件进、哪些文件不进、为什么"讲得一字不差。

## 2.1 阶段代码：先看全景，再逐层下钻

`run_review` 中扫描阶段的代码（暂不展开 `--changed` 分支）：

```python
    # ---------------------------------------------------------- 1. 扫描
    file_filter = FileFilter(
        workspace,
        excludes=list(settings.review.ignore_dirs),
        max_file_lines=settings.review.max_file_lines,
    )
    scan_result = scan(target, file_filter)
    files = scan_result.files

    if not files:
        raise ValueError(
            f"没有可审查的 Python 文件（{target}）。"
            f"跳过 {len(scan_result.skipped)} 个候选。"
        )

    _emit(options, "scan", {"files": len(files), "skipped": len(scan_result.skipped)})
```

四步，两个对象：

1. **构造 `FileFilter`**——筛选规则的载体。三个参数里的两个直接来自配置：`ignore_dirs`（排除目录表）和 `max_file_lines`（大文件阈值）。
2. **`scan(target, file_filter)`**——真正的遍历。注意它接收的是 **`target`（用户给的路径）** 而不是 `workspace`——审一个文件时从那个文件出发，审目录时才遍历整个目录。
3. **空结果保护**——一个都没筛出来时立刻报错（而不是带着空清单往下走、让后面每个阶段处理空列表）。错误消息带上跳过数量，方便定位"为什么一个文件都没留下"。
4. **发射 `scan` 事件**——CLI 的进度条看到的就是它。

这个阶段的产物是三个数据结构，后面所有阶段都在消费它们：

| 产物 | 类型 | 内容 | 谁用 |
|---|---|---|---|
| `files` | `list[Path]` | 通过筛选的文件绝对路径 | 确定性分析、`--changed` 过滤 |
| `sources` | `list[tuple[str, str]]` | （相对路径, 源码文本）对 | 规则引擎、外部分析器（2.5 节构建） |
| `file_inventory` | `list[tuple[str, int]]` | （相对路径, 行数）对 | 任务简报的文件清单（第 04 篇） |

## 2.2 `FileFilter`：四道筛子

`ingest/filters.py` 是筛选规则的实现。模块头注释先交代了判断顺序：

```python
"""文件筛选：路径级判断（是否属于审查范围）。

判断优先级：排除目录 > 扩展名 > .gitignore > 大小（大小由 scanner 读文件后判断）。
.gitignore 采用简易匹配（fnmatch + 路径前缀），覆盖常见用法；
完整 gitignore 语义（否定规则、** 等）不在 v1 范围。
"""
```

构造函数把配置翻译成三个字段：

```python
class FileFilter:
    """决定文件是否进入审查范围。"""

    def __init__(
        self,
        root: Path,
        excludes: list[str] | None = None,
        max_file_lines: int = 2000,
    ) -> None:
        self.root = Path(root).resolve()
        self.excludes = set(excludes or DEFAULT_EXCLUDES)
        self.max_file_lines = max_file_lines
        self._gitignore_patterns = self._load_gitignore()
```

| 参数 | 类型 | 默认 | 含义 | 调用方传入 |
|---|---|---|---|---|
| `root` | Path | 必填 | 工作区根目录（被 resolve 为绝对路径） | `workspace` |
| `excludes` | list[str] \| None | None → `DEFAULT_EXCLUDES` | 排除目录名集合 | `settings.review.ignore_dirs` |
| `max_file_lines` | int | 2000 | 大文件阈值（真正判断在 scanner 里） | `settings.review.max_file_lines` |

`excludes or DEFAULT_EXCLUDES` 又是一个 `or` 短路：传 None 用默认表，传了就全用你传的（不做合并）。默认表就是配置里那份（`.git` / `__pycache__` / `.venv` / `node_modules` / `build` / `dist` / `.codesage` 等 12 项）。

### 第一道：排除目录

两个函数分别负责"遍历时剪枝"和"逐文件判断"：

```python
    def is_excluded_dir(self, name: str) -> bool:
        """遍历时的目录剪枝判断。"""
        return name in self.excludes or name.endswith(".egg-info")
```

注意 `name.endswith(".egg-info")`——`*.egg-info` 是打包产物目录，名字带包名前缀无法枚举，所以用后缀规则兜住。另外 `should_include` 里对该路径**任意层级**的每一段目录都做检查：

```python
        # 任意层级的排除目录
        for part in rel.parts[:-1]:
            if self.is_excluded_dir(part):
                return False, f"排除目录：{part}"
```

`rel.parts[:-1]` 是"路径的所有目录段"（去掉最后一段文件名）——所以 `a/b/.venv/c/x.py` 和 `.venv/x.py` 都会被拦下。

### 第二道：扩展名

```python
        if path.suffix != ".py":
            return False, "非 Python 文件（v1 仅支持 Python）"
```

v1 只支持 Python——跳过原因本身就写明了原因（"v1"），这是刻意的：未来多语言版本不会让这份原因文案变成误导。

### 第三道：`.gitignore`（简易匹配）

加载时过滤三类行：

```python
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("!"):
                continue
            patterns.append(line.rstrip("/"))
```

空行、注释（`#`）、**否定规则（`!`）**都被跳过——注释里已坦白："完整 gitignore 语义（否定规则、`**` 等）不在 v1 范围"。匹配用 `fnmatch` 三种姿态：

```python
    def _matches_gitignore(self, rel_posix: str) -> bool:
        for pattern in self._gitignore_patterns:
            if pattern.startswith("/"):
                pattern = pattern[1:]
            if fnmatch.fnmatch(rel_posix, pattern):
                return True
            # 目录前缀：如 "docs" 匹配 "docs/xx.py"
            if "/" not in pattern and fnmatch.fnmatch(rel_posix, f"{pattern}/*"):
                return True
            if fnmatch.fnmatch(rel_posix, f"*/{pattern}"):
                return True
        return False
```

翻译成人话：`generated/` 这种目录名会命中 `generated/*`；`*.log` 这种通配会命中任意层级；`/build` 的前导斜杠会被剥掉按普通模式处理。**这是个"覆盖常见用法"的近似实现**——复杂模式（比如 `**/test/**`、`!keep.py`）不生效，测试文件 `test_ingest.py` 里也只锁定了"`generated/` 目录被排除"这类基本行为。知道边界在哪里，比假装支持更专业。

### 汇总：`should_include` 的契约

```python
    def should_include(self, path: Path) -> tuple[bool, str]:
        """路径级筛选。返回 (是否纳入, 跳过原因)。"""
```

**返回值是一个二元组**：第一个是判断结果，第二个是**人类可读的跳过原因**。这个"不进也要说清楚为什么"的契约，在 scanner 里被逐条记录，最终展示在报告的"已跳过"一栏——审查工具最忌讳黑箱，"它没看我这个文件"和"它没看我这个文件，因为匹配了 .gitignore 规则"是完全不同的体验。

## 2.3 `scan()`：遍历、剪枝与跳过记录

`ingest/scanner.py` 的 `scan()` 是遍历的入口。函数头注释：

```python
def scan(root: Path, file_filter: FileFilter) -> ScanResult:
    """遍历 root，应用筛选规则，返回文件清单。

    使用 os.walk(topdown=True) 直接剪枝排除目录，避免进入 .venv 等深目录。
    """
```

### 单文件场景

```python
    # 单文件场景
    if root.is_file():
        ok, reason = file_filter.should_include(root)
        if ok:
            result.files.append(root)
        else:
            result.skipped.append((root.name, reason))
        return result
```

`codesage review app.py` 走的就是这条路：只对这一个文件做筛选判断。注意 `skipped` 里存的是 `(文件名, 原因)` 二元组——这个二元组结构贯穿 `ScanResult`。

### 目录遍历：一次剪枝，三道检查

```python
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        # 剪枝：原地修改 dirnames 阻止递归
        dirnames[:] = [d for d in dirnames if not file_filter.is_excluded_dir(d)]
        for name in sorted(filenames):
            path = Path(dirpath) / name
            ok, reason = file_filter.should_include(path)
            if not ok:
                if reason != "非 Python 文件（v1 仅支持 Python）":
                    # 非 Python 文件太多，不逐条记录，只记录有意义的跳过
                    result.skipped.append((_rel(path, root), reason))
                continue
            lines = count_lines(path)
            if lines > file_filter.max_file_lines:
                result.skipped.append(
                    (_rel(path, root), f"大文件预过滤（{lines} 行 > {file_filter.max_file_lines}）")
                )
                continue
            if lines <= 1:
                result.skipped.append((_rel(path, root), "空文件或单行文件"))
                continue
            result.files.append(path)

    result.files.sort()
    return result
```

逐点拆解：

**剪枝（本函数性能上的灵魂）。** `os.walk` 每次迭代产出 `(dirpath, dirnames, filenames)`，其中 `dirnames` 是"接下来要递归进入的子目录列表"。`topdown=True` 模式下**原地修改 `dirnames`（注意 `dirnames[:] = ...` 是原地替换，不是重新绑定名字）**会直接改变递归计划——被删除的目录**根本不会被走进**。`.venv` 里可能有几千个文件，一个都不值得遍历，剪枝让它们连"被翻到"的机会都没有。

**跳过记录的分级。** 非 Python 文件的跳过**不逐条记录**（注释写明："非 Python 文件太多，不逐条记录，只记录有意义的跳过"）——一个前端项目里 `.js` 可能有几百个，全部记进 `skipped` 只会淹没真正重要的跳过原因。而"排除目录"、"gitignore"、"大文件"、"空文件"这四类**有诊断价值**的跳过会逐条记录。

**行数检查在 scanner 而不在 filter。** 因为行数要读文件才知道——`should_include` 只处理"不读内容就能判断"的路径级规则。`count_lines` 的实现很务实：

```python
def count_lines(path: Path) -> int:
    """快速统计行数（二进制读，不计编码问题）。"""
    try:
        with open(path, "rb") as f:
            return f.read().count(b"\n") + 1
    except OSError:
        return 0
```

**二进制模式读、只数字节 `b"\n"`**——不解码（不怕编码错误）、不按行 split（省内存），`+1` 是补偿"最后一行可能没有换行符"。读失败返回 0（后面会被"空文件"分支拦下）。

**排序。** `sorted(filenames)` 让同一目录内按名字稳定遍历，最后 `result.files.sort()` 保证整体顺序确定——**可复现**是审查工具的隐形需求（同样的输入，报告里发现顺序不该变来变去）。

### `ScanResult`：结果与透明度

```python
@dataclass
class ScanResult:
    """扫描结果。"""

    files: list[Path] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (相对路径, 原因)

    @property
    def total_candidates(self) -> int:
        return len(self.files) + len(self.skipped)
```

跳过原因的完整分类（从代码里收集，这也能当"为什么我的文件没被审"的排查手册）：

| 跳过原因 | 触发条件 | 记录粒度 |
|---|---|---|
| `排除目录：{名}` | 路径中任意一段在排除表里 | 逐条 |
| `非 Python 文件（v1 仅支持 Python）` | 扩展名不是 `.py` | 不记录 |
| `匹配 .gitignore 规则` | 命中三级 fnmatch 之一 | 逐条 |
| `大文件预过滤（N 行 > M）` | 行数超过 `max_file_lines` | 逐条 |
| `空文件或单行文件` | 行数 ≤ 1 | 逐条 |

这些记录最终流向报告 metadata 的 `scan_skipped` 字段（截取前 20 条），终端报告末尾会打印"已跳过 N 个文件，例如：……"（第 08 篇看渲染效果）。

> ### 【Python 加餐 7】pathlib 与 `with`（Java 对照）
>
> **`pathlib.Path`——路径操作的现代方式。** 本项目到处是 `Path`，它对应的 Java 类是 `java.nio.file.Path`，但 API 体验差别很大：
>
> ```python
> path = Path("/home/user/project")
> (path / "src" / "app.py")          # `/` 运算符拼路径（不是除号！）
> p.read_text(encoding="utf-8")      # 一行读整个文件
> p.write_text(text)                 # 一行写
> p.name; p.stem; p.suffix; p.parent # app.py / app / .py / 上级目录
> p.resolve(); p.relative_to(root); p.is_file(); p.exists()
> p.as_posix()                       # 统一转成 "a/b/c" 正斜杠形式（跨平台报告用）
> ```
>
> Java 写法对比：`Paths.get(a, b).resolve()` / `Files.readString(p)`。Python 版的杀手锏是 **`/` 运算符**——浏览器地址栏式的路径拼接，比 `os.path.join` 可读得多。本项目在 `_rel_posix()` 里用 `relative_to() + as_posix()` 生成"相对路径 + 正斜杠"的组合，作为报告里的统一路径形式（Windows 反斜杠、盘符差异在这里被抹平）。
>
> **`with` 上下文管理器——try-with-resources 的 Python 版本。**
>
> ```python
> with open(path, "rb") as f:
>     return f.read().count(b"\n") + 1
> ```
>
> 语义和 Java 一模一样：**进入时调用资源的 `__enter__`（打开文件），退出时无论正常还是异常都调用 `__exit__`（关闭文件）**。Java 里是 `try (var f = Files.newInputStream(p)) { ... }`，靠 `AutoCloseable` 接口；Python 靠"上下文管理器协议"——一个类只要实现了 `__enter__` / `__exit__` 两个方法，就能用在 `with` 里。文件对象、锁、数据库连接都是这样用的。对比记忆：**`with` ≈ try-with-resources，`__enter__/__exit__` ≈ `AutoCloseable.close()`（外加可选的异常处理返回值）**。
>
> 后面还会遇到另一种用法：`with` 不限"打开-关闭"，任何"进入/退出要成对"的场景都能打包，比如测试里的 `tempfile.TemporaryDirectory()`（第 09 篇）。

> ### 【Python 加餐 8】生成器（Java 对照）
>
> `os.walk(...)` 是理解生成器的最佳例子。它的语义是"深度优先遍历目录树"，但如果它把所有目录**一次性收集成一个列表再返回**，大仓库会瞬间吃掉几十 MB 内存。实际实现是**生成器**：每要一个目录，算一个，给你一个。
>
> ```python
> for dirpath, dirnames, filenames in os.walk(root, topdown=True):
>     ...   # 每循环一次，"现场"产生一个 (目录, 子目录表, 文件表) 三元组
> ```
>
> **生成器的语法标志是 `yield`：**
>
> ```python
> def countdown(n):
>     while n > 0:
>         yield n     # 产出一个值，暂停在这里；下次迭代从这里继续
>         n -= 1
> ```
>
> 调用 `countdown(3)` 不会执行任何循环体——它返回一个"能产出值的对象"；每次 `next()` 推进到下一个 `yield`。**核心差异：Java 的集合是"先全部算出来再遍历"，生成器/迭代器是"要一个算一个"（惰性求值）。** Java 也有对应物：`Iterator` 手写起来很啰嗦，`Stream` 的惰性流水线最接近哲学。
>
> 项目里没有手写 `yield`（需要产出的数据都是小规模），但生成器的思想在三个地方出现：
>
> 1. **`os.walk`**（库提供的生成器）——省下遍历 `.venv` 前的内存峰值；
> 2. **`Enum`、`dict.items()` 这些"可迭代对象"**——`for` 循环的通用协议是迭代器（本项目 `for severity in _SEVERITY_ORDER`、`for f in report.findings` 都是）；
> 3. **生成器表达式**——推导式的"惰性版"（第 03 篇【Python 加餐 11】细讲），比如 `any(f.severity.weight >= fail_level.weight for f in report.findings)`：不为每个元素建列表，边算边问到第一个满足就停。

## 2.4 `--changed` 增量模式：只审本次改动

日常开发最顺手的模式是 `codesage review . --changed`：**只审 git 变更涉及的文件**——省 token，也贴合真实工作流（提交前自查、CI 增量检查）。

### 语义：三种情况，三种行为

当前版本的边界语义（pipeline 注释原文）：

```python
    # --changed：只保留 git 变更涉及的文件（增量审查）
    # 边界语义：非 git 仓库 → 回退全量并提示；仓库但无变更 → 明确报错
    # （绝不静默全量——用户加 --changed 时期望的是增量，静默全量会浪费 Token）
    if options.changed_only:
        from codesage.ingest.git_diff import changed_line_ranges, is_git_repo

        if not is_git_repo(workspace):
            _emit(
                options,
                "changed_fallback",
                {"reason": "当前目录不是 git 仓库，已回退为全量审查"},
            )
        else:
            ranges = changed_line_ranges(workspace)
            changed_files = {(workspace / rel).resolve() for rel in ranges}
            if not changed_files:
                raise ValueError(
                    "未检测到 git 变更（对比 HEAD）。可暂存/修改文件后重试，"
                    "或去掉 --changed 进行全量审查。"
                )
            files = [f for f in files if f.resolve() in changed_files]
            if not files:
                raise ValueError(
                    f"变更的 {len(changed_files)} 个文件中没有可审查的 Python 文件。"
                )
```

| 场景 | 行为 | 为什么 |
|---|---|---|
| 非 git 仓库 | 回退全量 + 发射 `changed_fallback` 事件（CLI 黄色提示） | 目录不是仓库，"变更"无从谈起；但不该报错挡住审查 |
| 仓库但无变更 | **明确报错**（`ValueError`，退出码 2） | 用户加 `--changed` 就是要增量；静默全量会浪费 Token——把"没变更"当错误反而是最省心的语义 |
| 有变更 | 只保留变更文件;变更文件里没有 .py → 报错 | 正常路径 |

第三个报错（"变更的 N 个文件中没有可审查的 Python 文件"）是防呆：你改了 README，`--changed` 过滤后剩下零个 Python 文件——明确告诉你"改了东西但没有能审的"，而不是稀里糊涂报"没有可审查文件"。

### 实现：零依赖解析 `git diff`

`ingest/git_diff.py`。模块注释：

```python
"""git diff 解析：定位变更行范围（用于 --changed 增量审查模式）。

实现方式：解析 `git diff HEAD -U0` 的 hunk 头（@@ -a,b +c,d @@），
只取新增侧的行号范围。这是零依赖实现（不引入 GitPython）。
"""
```

三个公开函数 + 一个正则：

```python
HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
```

- `is_git_repo(path)`：`git rev-parse --is-inside-work-tree` 返回 `true` 才算（顺带处理了 git 未安装、命令超时等异常——一律返回 False）。
- `changed_line_ranges(repo, base="HEAD")`：跑 `git diff HEAD -U0 --no-color --no-ext-diff`，逐行扫描输出：

```python
    for line in proc.stdout.splitlines():
        if line.startswith("+++ "):
            # "+++ b/path/to/file.py" 或 "+++ /dev/null"
            target = line[4:].strip()
            if target.startswith("b/"):
                current = target[2:]
            else:
                current = None
            if current and current not in result:
                result[current] = []
            continue
        ...
        m = HUNK_RE.match(line)
        if m:
            start = int(m.group(1))
            length = int(m.group(2)) if m.group(2) else 1
            if length <= 0:
                continue
            result[current].append(HunkRange(start=start, end=start + length - 1))
```

`+++ b/...` 行切换"当前文件"；`@@ -旧 +新 @@` 行解析出新增侧的行号区间（`-U0` 表示 diff 不带上下文行，"新侧"就是从 `+` 开头的那个数字）。返回 `{相对路径: [HunkRange, ...]}`，一切失败（非仓库/超时/异常退出）返回空字典。

**诚实标注**：`git_diff.py` 里还有一个 `expand_ranges()`（把行区间向外扩展 padding 行，比如给模型多看几行上下文），有测试覆盖但**主链路当前没有调用**——也就是说 `--changed` 目前做到的是"**文件级**过滤"：选中的文件仍然整文件审查，"只把变更行附近喂给模型"的行区间级裁剪还没接线。任务简报里会写一句"本次为增量审查：只关注 git 变更涉及的文件与行区间附近的代码"，但具体行区间尚未注入。这是 v1 的简化边界（第 12 篇诚实清单会再汇总）。

## 2.5 读源码与文件清单：为后续阶段备料

扫描确认了文件清单之后，管线紧接着有一段"备料"代码（在源码里它位于"2. 确定性分析"段落的开头，但产出被多个阶段共用）——把文件读成内存中的源码，并同时构建文件清单：

```python
    # 一次读取全部源码（多处复用）
    sources: list[tuple[str, str]] = []  # (相对路径, 源码)
    for file_path in files:
        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = _rel_posix(file_path, workspace)
        file_inventory.append((rel, count_lines(file_path)))
        sources.append((rel, source))
```

三个决策都在注释里能找到理由：

**"一次读取全部源码（多处复用）"**——源码文本后面要被三个消费者使用：规则引擎逐文件解析、外部分析器批量扫描（Ruff/Bandit 需要）、任务简报的行数统计。读一遍存内存，比每个消费者各自读盘好。

**`errors="replace"`**——文件编码不是 UTF-8 时（GBK 文件、混入二进制），不抛异常而是用替换符 `�` 补位。审查工具不能因为一个"非标准编码的文件"而中止整场审查。

**读失败 `continue`**——`OSError`（权限、文件被删）直接跳过这个文件，不中断。

辅助函数 `_rel_posix` 把绝对路径变成"相对工作区的正斜杠路径"：

```python
def _rel_posix(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name
```

`relative_to` 在"路径不在 root 下"时会抛 `ValueError`，兜底用文件名。为什么需要 `as_posix()`？因为报告里的路径要**跨平台一致**——Windows 下 `Path.as_posix()` 会把 `a\b\c.py` 转成 `a/b/c.py`，保证同一条发现无论在哪台机器生成的报告里，路径写法都一样。

最后，这个阶段的收尾就是 2.1 看过的 `_emit(options, "scan", ...)`——"扫描完成：N 个文件（跳过 M）"。

> ### 【Python 加餐 9】f-string 与字符串方法（Java 对照）
>
> 本项目所有动态字符串都是 f-string（formatted string literal）：
>
> ```python
> f"大文件预过滤（{lines} 行 > {file_filter.max_file_lines}）"
> ```
>
> `f` 前缀 + `{}` 插值：**花括号里可以放任意表达式**——变量、方法调用、运算（`{start + length - 1}`）、甚至条件表达式。Java 对应 `String.format("...%d...", x)` 或 Java 15+ 的模板字符串（还是预览特性），写起来都没有 f-string 顺手。
>
> 几个后面会大量见到的进阶形态（先混个脸熟，第 06、08 篇还有专题）：
>
> ```python
> f"{i:>{width}} | {line}"     # 对齐：右对齐、宽度取自变量 width
> f"{1_234_567:,}"              # 千分位：1,234,567
> f"{cost:.4f}"                 # 定点小数：保留 4 位
> f"{ratio:.0%}"                # 百分比：62%
> ```
>
> 除了引号、插值、f-string 之外，项目最常用的字符串方法有三类：`str.strip()`（去首尾空白——用户输入清洗三连 `str(value).strip().lower()` 到处出现）、`str.splitlines()`（切成行列表，处理跨平台换行）、`"-".join(parts)`（用分隔符拼接列表——注意是"分隔符在前"，和 Java 的 `String.join(sep, list)` 是反着记的）。
>
> 还有一处**逐字转义**的记忆点：`f"\\n"` 表示"字面反斜杠 + n"（两个字符），而 `f"\n"` 才是真换行。第 06 篇会看到 `submit_finding` 里专门处理"模型把换行发成了字面 `\n`"的兼容逻辑——到时候你会庆幸现在记住了这条区别。

## 2.6 小结与下一站

- **阶段一的全部输入**：`workspace`、`ignore_dirs`、`max_file_lines`；**全部输出**：`files` / `sources` / `file_inventory`。
- **四道筛子**：排除目录（任意层级 + `.egg-info` 后缀）→ 扩展名（仅 `.py`）→ `.gitignore` 简易匹配（fnmatch 三级，边界已坦白）→ 大小/空文件（scanner 内、逐条记录原因）。
- **一次剪枝**：`os.walk(topdown=True)` + 原地修改 `dirnames`，被排除目录不进入。
- **跳过透明**：`ScanResult.skipped` 记录（路径，原因）二元组，最终展示在报告里。
- **`--changed` 三态语义**：非 git 回退+提示 / 无变更报错 / 有变更过滤；`git diff HEAD -U0` 零依赖解析 hunk 头；行区间级裁剪（`expand_ranges`）已备未接线。
- **读源码**：一次读取、`errors="replace"` 容错、读取失败跳过；`_rel_posix` 统一相对 POSIX 路径。
- **Python 加餐**：pathlib 与 `with`（vs try-with-resources）、生成器（vs 惰性迭代）、f-string 与字符串方法。

下一站进入**阶段二 · 确定性分析**：64 个文件（或任意数量）的源码已经在内存里，接下来是零 Token 的"第一网"——13 条自研 AST 规则 + 3 个外部分析器。第一个函数调用是 `RuleEngine()` 的构造，以及一条你可能没想到的细节：**所有规则共享同一棵语法树**。

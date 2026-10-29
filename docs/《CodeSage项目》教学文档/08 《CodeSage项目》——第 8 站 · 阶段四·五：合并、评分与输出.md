# 《CodeSage项目》——第 8 站 · 阶段四·五：合并、评分与输出

> 📍 **主线进度**：起点 ✅ → 扫描 ✅ → 确定性分析 ✅ → 组装 ✅ → 循环 ✅ → 工具 ✅ → 后勤 ✅ → **▶ 合并与输出（本篇，主线终点）**

循环无论从哪个出口退出，桌上都摆着三个账本：`det_findings`（确定性候选）、`llm_findings`（模型提交的）、`dismissed`（被驳回的）。这一篇完成收网：驳回过滤 → 跨来源去重 → 评分 → 落盘 → 渲染。走完这一站，你手里就是用户真正拿到的那份报告。

## 8.1 合并段代码：三个账本合成一份报告

先把整段代码过一遍：

```python
    # ---------------------------------------------------------- 4. 合并与聚合
    merged = _merge_findings(det_findings, llm_findings, dismissed)
    duration = time.perf_counter() - started

    report = ReviewReport(
        target=str(target),
        findings=merged,
        duration_sec=duration,
        usage=loop_usage,
        turns=turns,
        llm_enabled=llm_used,
        stop_reason=stop_reason,
        compress_events=compress_events,
        files_scanned=len(files),
        files_skipped=len(scan_result.skipped),
        metadata={
            "profile": options.profile,
            "cost_yuan": 0.0,
            "candidates": len(det_findings),
            "llm_findings": len(llm_findings),
            "dismissed": dismissed,
            "analyzers": [a.name for a in analyzers],
            "scan_skipped": [
                {"file": f, "reason": r} for f, r in scan_result.skipped[:20]
            ],
        },
    )

    # 成本估算（若有 LLM 调用）
    if llm_used:
        report.metadata["cost_yuan"] = round(
            loop_usage.cost_yuan(
                settings.budget.input_price_per_m,
                settings.budget.output_price_per_m,
            ),
            4,
        )
```

三个值得注意的点：

- **`time.perf_counter()`**——计时用的高精度单调时钟（不受系统时间调整影响），`duration_sec` 报告里的"耗时 N.Ns"来源；
- **`metadata` 是一本"过程账"**：profile、成本（先置 0，有 LLM 再算）、候选数/LLM 发现数（对比"复核了多少、新增了多少"）、驳回记录全文（可审计）、分析器清单、跳过的文件（截取前 20 条）。**报告不只说"发现了什么"，也说"过程发生了什么"**；
- **成本只在走过 LLM 时计算**——零成本的确定性分析不写"¥0.0000"以外的噪音（准确说 cost_yuan 保持初始 0.0）。

然后是阶段五（落盘）：

```python
    # ---------------------------------------------------------- 5. 落盘
    if store is not None:
        try:
            review_id = store.save_review(report, project=str(workspace))
            report.metadata["review_id"] = review_id
        except Exception:  # noqa: BLE001 - 落盘失败不致命
            pass
        finally:
            store.close()
```

落盘失败**静默吞掉**（只读目录、权限问题的场景）——但成功时把 `review_id` 写回 metadata（终端的"落盘 #N"，以及后续 `history --detail N` 的引用方式）。`project=str(workspace)`——**每条记录带上"这是哪个项目"的标签**，为 history 过滤和错题本隔离做准备（8.4 节细讲）。

最后发射完成事件、返回报告：

```python
    _emit(
        options,
        "done",
        {
            "findings": len(merged),
            "score": report.score,
            "duration": round(duration, 2),
        },
    )
    return report
```

## 8.2 驳回过滤与去重：`_merge_findings`

```python
def _merge_findings(
    det_findings: list[Finding],
    llm_findings: list[Finding],
    dismissed: list[dict],
) -> list[Finding]:
    """合并确定性发现与 LLM 发现，并应用驳回过滤。

    驳回过滤：候选发现（来自规则/外部工具）若被 LLM 复核为误报，
    从最终报告中移除（容差 ±2 行）。
    """
    if dismissed:

        def is_dismissed(finding: Finding) -> bool:
            if finding.source.value == "llm":
                return False
            for d in dismissed:
                if d["file"] != finding.file:
                    continue
                if abs(d["line"] - finding.line_start) <= 2:
                    return True
            return False

        det_findings = [f for f in det_findings if not is_dismissed(f)]

    return dedup_findings([*det_findings, *llm_findings])
```

**驳回过滤**（第 06 篇 6.3 已讲透，这里复习两个约束）：`±2 行`容差解决"模型引用行号与规则原始行号差一两行"；LLM 来源直接豁免（驳回只作用于"规则的候选"）。

注意 `[*det_findings, *llm_findings]`——`*` 解包语法把两个列表"摊开"进一个新列表（等价于 `det_findings + llm_findings`，不修改原对象）。合并顺序把**确定性发现放前面**——但别急，去重会重新裁决相同位置的归属。

**去重**是 `dedup_findings`（第 01 篇瞥过一眼，现在完整读）：

```python
def dedup_findings(findings: list[Finding]) -> list[Finding]:
    """按 dedup_key 分组去重。

    去重规则（解决跨工具重复报告，同时不误伤同一工具的独立发现）：
    - 组内只有单一来源 → 全部保留（同行上的两条自研规则发现是不同问题，
      例如"裸 except"与"异常被吞没"）；
    - 组内有多个来源（如 Bandit 与自研规则报了同一处问题）→ 只保留
      优先级最高来源的条目。

    来源优先级：LLM（语义确认过）> 自研规则（中文描述与修复建议最完整）
    > Bandit > Ruff > radon。
    """
    source_priority = {
        Source.LLM: 0,
        Source.AST_RULE: 1,
        Source.BANDIT: 2,
        Source.RUFF: 3,
        Source.RADON: 4,
    }

    groups: dict[str, list[Finding]] = {}
    for f in findings:
        groups.setdefault(f.dedup_key, []).append(f)

    result: list[Finding] = []
    for group in groups.values():
        if len(group) == 1:
            result.extend(group)
            continue
        sources = {f.source for f in group}
        if len(sources) == 1:
            result.extend(group)  # 单一来源：均为独立发现，全部保留
            continue
        best_source = min(sources, key=lambda s: source_priority.get(s, 9))
        result.extend(f for f in group if f.source == best_source)
    return result
```

分组键是 `Finding.dedup_key`（`文件:行号//3:类别`，第 01 篇）。三档裁决：

| 组内情况 | 裁决 | 为什么 |
|---|---|---|
| 只有 1 条 | 保留 | 没什么可争的 |
| 多条、**单一来源** | **全部保留** | 同行的两条自研规则发现可能是**不同问题**（"裸 except"与"异常被吞没"共享一个 except 块）；同来源多条是独立信息 |
| 多条、多来源 | 只留优先级最高的来源 | 跨工具报同一处 = 一件事被几个人指出，选最优的表述 |

三个实现细节：`groups.setdefault(key, []).append(f)`——字典分组的经典写法（键不存在就建空列表再 append）；`sources = {f.source for f in group}`——**集合推导式**统计"涉及几种来源"（加餐 11 的实例）；`min(sources, key=lambda s: source_priority.get(s, 9))`——按优先级权重取最小的来源（`get(s, 9)` 的兜底 9 保证未知来源排最后）。

> **这段优先级是怎么来的？** 第 11 篇会展开的第二起真实事故：demo 演示时发现 Bandit 的英文发现（B307）把自研规则的中文发现（CG011，带具体修复建议）挤掉了——`tests/test_models.py` 的文件头注释原样记录了这件事，这版优先级就是那次修复的产物。"LLM 第一"的含义则在守则 1 里明示："去重时你的版本优先于工具原始版本"——**人工校准过优先级的表，每一行都有因果**。

> ### 【Python 加餐 26】排序与 lambda key（Java 对照）
>
> 去重里那句 `min(sources, key=lambda s: source_priority.get(s, 9))` 用了 Python 排序家族的招牌姿势——**`key=` 接收一个"取排序键"的函数**：
>
> ```python
> sorted(findings, key=lambda f: (order[f.severity], f.file, f.line_start))  # 三元组做键
> min(sources, key=lambda s: source_priority.get(s, 9))                      # 按权重取最小
> sorted(dfs, key=lambda item: (-item[0], item[1].id))                       # 负号 = 降序
> ```
>
> 对照 Java：`Comparator.comparing(Finding::getSeverity).thenComparing(Finding::getFile)`——同样的"多级比较"，Python 的做法是"**造一个天然可比较的元组**"：Python 的元组按位字典序比较（第一项相等比第二项……），一行搞定 Java 的 thenComparing 链。
>
> `lambda` 是"一行匿名函数"：`lambda s: source_priority.get(s, 9)` 等价于一个只 `return` 这个表达式的函数。**和 Java lambda 的关键差异**：Python 的 lambda **只能是一个表达式**——不能有语句（不能写 `if/for/赋值`，值判断可用三元表达式），多行逻辑请老老实实写 `def`。项目里的 lambda 全部是一行取键或一行回调。
>
> 两个实用点：**稳定性**——`sorted` 是稳定排序（相等元素保持原顺序），所以"先按行号排、再按严重级排"能实现多轮排序拼装；**降序**的惯用法是"数字取负、字符串用 `reverse=True`"，混合场景就用元组（`(-score, name)` 表示"score 降序、name 升序"）。`sorted_findings()`（8.3 节）用的正是这个模式。

## 8.3 评分与统计：让报告可说人话

`ReviewReport` 的三组"视图"在第一篇见过定义，这里补上使用场景。评分：

```python
    @property
    def score(self) -> int:
        """0-100 评分：100 起步按严重级扣分。"""
        score = 100
        for f in self.findings:
            score -= f.severity.weight
        return max(0, score)
```

100 起步、逐条按 P0/P1/P2/P3 扣 15/8/3/1、下限截 0。**简单到一句话能讲清、任何分数都能追溯到具体发现**——不可解释的评分没法用来做质量讨论。

排序视图（终端分组的依据）：

```python
    def sorted_findings(self) -> list[Finding]:
        """严重级降序 → 文件 → 行号。"""
        order = {
            Severity.CRITICAL: 0,
            Severity.MAJOR: 1,
            Severity.MINOR: 2,
            Severity.INFO: 3,
        }
        return sorted(
            self.findings,
            key=lambda f: (order[f.severity], f.file, f.line_start),
        )
```

统计视图：

```python
    def by_severity(self) -> dict[str, int]:
        counter: dict[str, int] = {s.value: 0 for s in Severity}
        for f in self.findings:
            counter[f.severity.value] += 1
        return counter
```

（`by_category` 同构。）注意两个小设计：`order` 字典把"枚举 → 排序号"显式列出（枚举本身没定序）；`{s.value: 0 for s in Severity}` 用**字典推导式**预置全部键为 0——保证即使某级别零条，统计表也带着 `P0: 0` 的键（报告上显示"P0 0"比"少一个格子"清晰）。

## 8.4 落盘：全局存储与五张表

```python
def _open_store(store_path: str = "") -> Store | None:
    """打开全局存储（失败返回 None，不影响审查主流程）。

    默认位置：~/.codesage/store.db（用户级，不污染被审查项目）；
    可通过配置 review.store_path 覆盖（测试用）。
    """
    try:
        from codesage.memory.store import default_store_path

        path = Path(store_path) if store_path else default_store_path()
        return Store(path)
    except Exception:  # noqa: BLE001
        return None
```

**这里是本轮的一次重要演进**：存储从"被审查目录/.codesage/"迁到了**用户级全局 `~/.codesage/store.db`**。`store.py` 的模块注释把两个真实问题写得很完整：

```python
"""SQLite 持久化：审查历史 / 反馈 / 错题本 / Prompt 版本。

存储位置（用户级全局存储）：
    默认 ~/.codesage/store.db（可用配置 review.store_path 覆盖）

设计演进说明（修复两个真实问题）：
- 旧方案把库写在"被审查目录/.codesage/"下：一来会**污染被审查项目**
  （审查第三方代码也在人家目录里建文件夹），二来记录散落在各子目录，
  导致 history 看不到（用户实际反馈的困惑）；
- 新方案全局集中存储，用 project 列区分项目（值为审查根目录的绝对路径）：
  - reviews 记录每次审查的 project，history 默认展示全部、可 --project 过滤；
  - feedback / lessons 按 project 隔离——错题本经验只注入同项目的审查
    （project 为空的经验视为通用，对所有项目生效）。
"""
```

一个存储位置的选择，背后是两条产品判断：**"审查工具不该在别人的仓库里留下文件"** + **"历史记录要能一处查全"**。之前"审了 A 目录、又在 B 目录跑 history 看不到记录"正是分散存储的必然结果。

### 五张表的职责

`_SCHEMA` 建四张表（第五张后面说）：

| 表 | 列（关键） | 谁写 | 谁读 |
|---|---|---|---|
| `reviews` | target, **project**, score, findings_count, duration_sec, tokens, cost_yuan, payload(JSON) | 每次审查落盘 | `history`（列表与详情） |
| `feedback` | finding_id, verdict, **project**, category, file, title, note | `feedback` 命令 | 错题本提炼 |
| `lessons` | kind, **project**, pattern, content, hits | `feedback --mine` | 系统提示词注入 |
| `prompts` | profile, version, template, note | （PromptRegistry，未接线） | （预留） |

注意每个业务表都带 **`project` 列**——隔离语义是三档的：

| 查询方 | 语义 |
|---|---|
| `reviews` | 默认全部；`--project` 精确过滤 |
| `feedback` / `lessons` | **该项目 + 通用（project 为空）**——通用经验对所有项目生效 |

`lessons` 查询的实现（SQL 动态拼 `WHERE`）：

```python
        if project is not None:
            conditions.append("(project = ? OR project = '')")
            params.append(project)
```

### 向后兼容：`_migrate()`

旧库（没有 project 列）不能直接报错——自动补列：

```python
    def _migrate(self) -> None:
        """向后兼容：为旧库（无 project 列）补列。"""
        for table in ("reviews", "feedback", "lessons"):
            cols = {
                str(row[1])
                for row in self._conn.execute(f"PRAGMA table_info({table})")
            }
            if cols and "project" not in cols:
                self._conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN project TEXT DEFAULT ''"
                )
```

`PRAGMA table_info` 拿到每张表的列清单（第 2 列是列名）；缺 `project` 就 `ALTER TABLE ... ADD COLUMN` 补上（默认空串 = 通用）。**数据库迁移不是只有"大厂"才需要**——CLI 工具一次字段演进就得处理用户手里的旧库，这段 12 行的迁移代码就是最小可行的版本（`tests/test_store.py` 锁定了"旧库补列后隔离语义正确"）。

### 写入：`save_review`

```python
    def save_review(self, report: ReviewReport, project: str = "") -> int:
        cur = self._conn.execute(
            """INSERT INTO reviews
               (target, project, score, findings_count, duration_sec,
                tokens, cost_yuan, payload)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                report.target,
                project,
                report.score,
                len(report.findings),
                round(report.duration_sec, 2),
                report.usage.total,
                round(report.metadata.get("cost_yuan", 0.0), 4),
                json.dumps(report.to_dict(), ensure_ascii=False),
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid or 0)
```

两套存储**并存**：结构化列（score/findings_count/…）用于列表页快速查询；`payload` 存整份 `to_dict()` JSON 用于完整还原（`history --detail` 用 `ReviewReport.from_dict(payload)` 重建报告——第 10 篇）。`commit()` 每次写立即提交（CLI 场景没有批量事务的必要）。

> ### 【Python 加餐 27】sqlite3 与 json：标准库里的"小数据库"（Java 对照）
>
> **sqlite3 是 Python 标准库自带的嵌入式数据库**——零安装、单文件、支持完整 SQL。Java 里对应的 JDBC 需要驱动 jar + 连接池配置；Python 里：
>
> ```python
> import sqlite3
> conn = sqlite3.connect("store.db")     # 文件不存在就创建（≈ 打开连接）
> conn.row_factory = sqlite3.Row         # 让查询结果支持按下标和列名访问
> conn.execute("INSERT INTO t (a) VALUES (?)", ("x",))   # 参数化查询——防注入
> conn.commit()                          # 提交（Python 的默认隔离级别是"手动提交"）
> conn.execute("SELECT * FROM t").fetchall()             # 取全部行
> ```
>
> 与 Java 的三个对照点：**没有 Connection/Statement/ResultSet 三件套**——连接对象直接执行 SQL；**参数占位符是 `?`**（不是 `%s`，也没必要 PreparedStatement 对象——sqlite3 的 `?` 本身就是参数绑定，**永远别用字符串拼接 SQL**）；`row_factory = sqlite3.Row` 之后行对象可以像字典和元组一样访问（`row["score"]` 或 `row[0]`）。
>
> **json 模块**（加餐 14 见过一面）在这里承担"对象 ↔ 文本"的桥：
>
> ```python
> json.dumps(report.to_dict(), ensure_ascii=False)   # 对象 → 字符串 → 存进 TEXT 列
> json.loads(row["payload"])                          # 字符串 → dict → from_dict 还原
> ```
>
> **"关系列 + JSON 大字段"的混合存储**是轻量工具的常见选择：常用字段拆列（能 WHERE、能 ORDER BY），完整对象进 JSON（不丢信息）。比"全量正规范化拆十张表"务实得多——项目里唯一的 JSON 反序列化风险（脏数据、手工改库）用 `try/except json.JSONDecodeError` 兜住（`get_review_payload` 就是这么写的，坏数据返回 None 而不是炸掉 history）。

## 8.5 输出：渲染、导出与退出码

### 终端渲染：把报告变成"愿意看"的输出

`cli/render.py` 的原则（头注释）：

```python
"""Rich 终端渲染：把报告变成让人愿意看的输出。

渲染原则：
- 概览先行（分数 / 统计 / 成本一目了然）；
- 按严重级分组，P0/P1 完整展开（说明 + 建议），P2/P3 紧凑列出；
- 来源与置信度透明（用户知道每条是"规则报的"还是"LLM 确认的"）。
"""
```

**概览面板**（`render_summary`）：评分行（按分数着色：≥90 绿、≥75 黄、≥60 橙、其余红）+ 严重级四格 + 类别分布 + 一行元信息（文件数、耗时、模式、Token/成本、轮次、压缩次数、结束原因）。元信息是动态拼的：

```python
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
```

**"有 LLM 调用才显示 Token/轮次、有压缩才显示压缩"**——零成本模式下报告不会出现"Token 0（¥0.000）"这种噪音。

**分组渲染**（`_render_group`）：按 P0→P3 分组，每条完整展开标题、描述、建议、来源、置信度，P0/P1 额外显示证据代码行。这里有一处本轮修复的细节——**escape() 保护**：

```python
        console.print(f"   {escape(f.title)}", style="bold")
        if f.description:
            console.print(f"   [dim]{escape(f.description)}[/dim]")
```

Rich 把 `[xxx]` 当样式标记解析——而**发现内容里全是 `[B608]`、`["complexity"]` 这种方括号**！不 escape 的话这些文本会被当成标记吞掉或报错（第 01 篇讲过同类问题：JSON 输出要 `markup=False`）。多行建议也做了处理——按行缩进展开（修复代码块能保持形状）：

```python
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
```

**跳过列表**（`render_skipped`）：报告末尾展示"已跳过 N 个文件，例如：- xxx（原因）"——扫描阶段的透明度在这里兑现（第 02 篇）。

### 导出：Markdown 与 JSON

`cli/exporter.py` 两个纯函数 + 一个落盘函数：

- `to_markdown(report)`：标题 + 评分 + 统计表 + 逐条详情（badge 用 🔴🟠🟡🔵 对应 P0–P3；位置/来源/置信度行；证据代码块；引用块建议）——**贴进 PR 评论或文档**的形态；
- `to_json(report)`：`json.dumps(report.to_dict(), ensure_ascii=False, indent=2)`——**给 CI / 其他工具消费**的形态；
- `export_report(report, output, fmt)`：按格式写文件（父目录自动创建）。

导出路径的解析（`_resolve_output_path`，第 01 篇 1.2 节已读）和 Rich 直印的 `markup=False, soft_wrap=True`（第 01 篇）两处防御不再重复。

### 退出码

`review()` 命令最后四行（第 01 篇读过）：存在达到 `--fail-on` 阈值的问题 → `raise typer.Exit(1)`。报告输出与退出码的组合就是 CI 集成的全部契约：

```text
codesage review . --fail-on major --format json -o report.json
    → 退出码 0/1 做门禁；report.json 做归档
```

## 8.6 实测：demo_target 的完整报告

用 `examples/demo_target`（6 个刻意植入缺陷的文件）跑一次零成本模式：

```bash
codesage review examples/demo_target --no-llm
```

真实的终端输出（节选）：

```text
┌───────────────────────────── CodeSage 审查报告 ─────────────────────────────┐
│                                                                             │
│  评分 0 / 100                                                               │
│                                                                             │
│  严重级  P0 0   P1 10   P2 13   P3 4                                        │
│  类别    quality 9 | bug 5 | performance 3 | security 9 | best_practice 1   │
│                                                                             │
│  文件 6 个  ·  耗时 1.5s  ·  模式 仅确定性分析（零 Token）                  │
│                                                                             │
└─────────── D:\WorkBuddyWorkingDir\CodeSage\examples\demo_target ────────────┘

━━ P1 · 重要（10）━━

 #3127b3d4 [P1][安全] admin_tools.py:9
   使用了 os.system()
   os.system / os.popen 经 shell 执行命令，拼接外部输入会构成命令注入。
   → 改用 subprocess.run([...], shell=False) 传参数列表
   来源：规则 · CG013 · 置信度 100%
   代码：os.system(command)
```

对照着读这份输出：6 个文件、27 条发现（P1 10 / P2 13 / P3 4）、评分 0（靶场就是拿来扣分的——满分 100 被 10×8+13×3+4×1 扣穿，这正好验证了评分公式）；每条发现都带短 ID（`#3127b3d4`，反馈时引用）、位置、来源、规则号、置信度和证据代码。**评分 0 不是故障，是"故意满是缺陷的靶场"的预期结果**——把它当"负样本"看：一个干净的项目跑出来应该接近 100。

> ### 【Python 加餐 28】三元表达式与输出格式化（Java 对照）
>
> 渲染层是"表达式风格"最密集的地方，最后补两块拼图。
>
> **三元表达式**：Python 写成 `值1 if 条件 else 值2`（Java 是 `条件 ? 值1 : 值2`）——**条件在中间**，读作"取 值1，如果 条件，否则 值2"：
>
> ```python
> mode = "LLM + 确定性分析" if report.llm_enabled else "仅确定性分析（零 Token）"
> style = "green" if score >= 90 else ("yellow" if score >= 75 else "red")
> ext = "json" if fmt == "json" else "md"
> ```
>
> 链式嵌套时 Python 版本反而更线性（条件就近），但超过两层还是拆开写。注意它只是"表达式"——不能塞语句，所以渲染代码里"要么加一行、要么不加"的逻辑用 `if/extend` 而不用三元。
>
> **格式化输出的完整工具箱**（加餐 21 是 f-string 语法，这里是渲染层实际用法合集）：
>
> ```python
> f"{report.duration_sec:.1f}s"          # 3.14159 → "3.1s"
> f"{usage.total:,}"                     # 12345 → "12,345"
> f"¥{cost:.3f}"                         # 0.04 → "¥0.040"
> f"{f.confidence:.0%}"                  # 0.9 → "90%"
> f"[{style}]…[/{style}]"                # Rich 的样式标记（终端专用）
> "  ·  ".join(extras)                   # 用中点拼接列表
> ```
>
> 一个字符串 API 的隐藏细节：**`str.join` 是"分隔符在前"**（`", ".join(items)`），和 Java `String.join("," , items)` 的参数顺序正好相反——集体记忆口诀："Python 的分隔符会先说话"。
>
> 最后把 Rich 的角色再明确一次：它不是"格式化库"，是**终端渲染库**——`[bold]`、`[dim]`、`Text.assemble`、`Panel`、`Table` 这些是"'富文本标记'（render 层的事）"；而 `to_markdown`/`to_json` 是纯字符串（文件层的事）。两套输出走两条路，互不污染——**这就是为什么终端渲染的代码里满是 `[xxx]` 标记，而导出的 Markdown 里干净得没有一个**。

## 8.7 主线完结：一次审查的完整账本

回到 `run_review` 的返回值——主线八个站，每一站的产出都对得上账：

| 来源 | 产物 | 最终去处 |
|---|---|---|
| 阶段一 · 扫描 | `files` / `sources` / `file_inventory` | 分析输入、简报、报告统计 |
| 阶段二 · 确定性分析 | `det_findings`（候选） | 简报、合并（过滤后进报告） |
| 阶段三 · 循环 | `llm_findings` / `dismissed` / `usage` / `turns` / `stop_reason` | 合并、报告字段 |
| 阶段四 · 合并 | `merged`（最终发现） | 报告 + score + 统计 |
| 阶段五 · 落盘 | `review_id` | `history` 回查 |

主线到此走完。三条件支线——评测（09）、自优化（10）、工程实录（11）——各自走同一条主干道或围绕它工作，下一篇从 `codesage eval` 命令开始。

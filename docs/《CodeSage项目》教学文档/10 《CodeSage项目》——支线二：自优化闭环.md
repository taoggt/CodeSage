# 《CodeSage项目》——支线二：自优化闭环

> 📍 **主线进度**：主线（01–08）已完结；本篇是**支线二**。自优化不是审查的环节，而是"让下一次审查比这一次更准"的机制——它把人工反馈喂回第 04 篇的提示词组装。

纠错闭环解决的是工具的"成长问题"：**每次审查都从零开始，犯过的错还会再犯。** 自优化的目标就一句话——让工具从"每次从零开始"进化到"**越用越准**"，并且每一步优化都有数据验证，不是"感觉变好了"。这一篇讲三个环的设计与两个半的落地。

## 10.1 闭环总览：从一条反馈到下一次注入

完整链路（走一遍）：

```text
① 用户在报告里看到一条判定失误的发现（短 ID #0f9a2657）
        │  codesage feedback 0f9a2657 -v wrong -n "该场景下是刻意的"
        ▼
② feedback 表（全局库 ~/.codesage/store.db）
   {finding_id, verdict, project, category, file, title, note}
        │
        │  codesage feedback --mine   ← 提炼
        ▼
③ lessons 表
   {kind: false_positive, project: <项目路径>, content: "【误报案例】…"}
        │
        │  下次审查开始时（pipeline 装配段）
        ▼
④ LessonBook(store).few_shot_block(project=str(workspace))
        │  → 生成经验文本（限 6 条 / 1200 字符）
        ▼
⑤ build_system_prompt(..., lessons_block=...) → "## 历史经验" 段进入系统提示词
```

这条链上的每一步都有确定的代码位置：`optim/feedback.py`（采集）、`optim/lessons.py`（提炼与注入文本）、`pipeline.py`（读取注入）、`agent/profiles.py`（拼进提示词）。**本轮的重要演进**：每一环都带上了 `project` 维度——经验和它诞生的项目绑定（10.3 节细讲）。

三个环的规划 vs 落地：

| 环 | 内容 | 规划优先级 | 当前状态 |
|---|---|---|---|
| 环 1 | 反馈采集 → 错题本 → Prompt 注入 | 必做 | **已闭环**（本篇 10.2–10.3） |
| 环 2 | Prompt 版本管理 + 效果绑定 | 必做 | **半落地**：注册表已实现、主链路未接线（10.5） |
| 环 3 | 参数自适应（压缩阈值、路由自动调优） | 选做 | 未实现，设计思路保留（v2） |

## 10.2 `codesage feedback` 命令：把一次判定变成数据

```python
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
```

| 用法 | 作用 |
|---|---|
| `feedback <id> -v wrong -n "…"` | 标记误报（优化原料之一） |
| `feedback <id> -v right` | 确认真问题（对照数据） |
| `feedback <id> -v missed -n "…"` | 标记漏报（另一份原料） |
| `feedback --mine` | 从反馈表提炼经验进错题本 |
| `feedback --show` | 查看错题本（带项目列） |

### 三态判定

`optim/feedback.py` 定义合法判定与中文标签：

```python
VALID_VERDICTS = ("wrong", "right", "missed")

_VERDICT_ZH = {
    "wrong": "误报（判定有误）",
    "right": "确认（确实有问题）",
    "missed": "漏报（应该发现但没发现）",
}
```

`record_feedback()` 在写入前校验 verdict（非法值抛 `ValueError`），然后落库：

```python
@dataclass
class FeedbackEntry:
    """一条人工反馈。"""

    finding_id: str
    verdict: str  # wrong / right / missed
    project: str = ""   # 审查根目录绝对路径（错题本按项目隔离的依据）
    category: str = ""
    file: str = ""
    title: str = ""
    note: str = ""
```

### 自动补上下文：`_locate_finding`

用户敲命令时只提供 8 位短 ID——其余上下文（category/file/title/**project**）由命令自动从历史记录里反查：

```python
def _locate_finding(store: Store, finding_id: str) -> tuple[dict | None, str]:
    """在最近 50 次审查中定位一条发现，同时返回它所属的项目。"""
    for row in store.recent_reviews(limit=50):
        for finding in store.get_review_findings(int(row["id"])):
            if str(finding.get("id")) == finding_id:
                return finding, str(row.get("project") or "")
    return None, ""
```

逐层看：遍历**最近 50 次审查**（`recent_reviews`）→ 每次审查的 findings（从 payload JSON 里解出）→ 比对 `finding["id"]`（就是第 01 篇那个 SHA1 短 ID）。找到就**连同它所属的项目路径一起返回**——project 从这里"继承"给反馈记录（错题本隔离的依据）。找不到也不挡路（提示后照常记录，只是上下文为空）：

```python
        located, project = _locate_finding(store, finding_id)
        if located is None:
            console.print(
                f"[yellow]在历史记录中未找到 #{finding_id}（仍会记录反馈，"
                "但无法自动填充上下文）。[/yellow]"
            )
            located = {}
```

**"工具对人的要求越少，人越愿意反馈"**——反馈频率是自优化系统的生命线，这个 30 行的反查函数就是为体验服务的。记录成功后的提示也明确指路：

```text
已记录反馈 #3：0f9a2657 → 误报（判定有误）（项目：my_project）
提示：运行 `codesage feedback --mine` 把反馈提炼为错题本经验。
```

## 10.3 错题本：提炼、隔离与注入

### 提炼：`mine_from_feedback`

`optim/lessons.py` 的模块头先画闭环，再立纪律：

```python
"""错题本：误报/漏报模式沉淀，注入 Prompt 形成自优化闭环。

闭环逻辑：
    人工反馈（feedback 表）
        → mine_from_feedback() 提炼为 Lesson（lessons 表）
        → few_shot_block() 生成经验文本
        → 注入 system prompt（agent/profiles.build_system_prompt 的 lessons_block）

防过拟合纪律（面试要点）：
- 注入的经验条数有上限（few_shot_block 的 limit）；
- 效果必须通过 Harness A/B 评测验证（不能"感觉变好了"）；
- 评测数据集拆分调优集/验证集（见 README 的评测章节）。
"""
```

提炼函数把每条 `wrong` / `missed` 反馈变成一条经验文本：

```python
    def mine_from_feedback(self) -> dict[str, int]:
        """从反馈中提炼经验。返回 {"false_positive": n, "false_negative": m}。

        每条反馈自带 project（记录反馈时所在的项目），提炼时保持项目隔离。
        """
        added = {"false_positive": 0, "false_negative": 0}
        for fb in self._store.all_feedback(limit=500):
            verdict = str(fb.get("verdict", ""))
            project = str(fb.get("project") or "")
            if verdict == "wrong":
                content = (
                    f"【误报案例】{fb.get('file', '?')} "
                    f"{fb.get('title', '?')}"
                    + (f"：{fb.get('note')}" if fb.get("note") else "")
                )
                if self.add(
                    LESSON_FALSE_POSITIVE, content, str(fb.get("category") or ""), project
                ) > 0:
                    added["false_positive"] += 1
            elif verdict == "missed":
                ...
```

四个细节：

- **只处理 `wrong` 和 `missed`**——`right` 是点赞，不产生经验（它是评测的对照数据，不是优化原料）；
- **经验文本的模板**：`【误报案例】<文件> <标题>：<备注>`——文件+标题给出"在哪、什么问题"，用户备注补充"为什么例外"；
- **去重**：`add()` 按 `kind + content + project` 查重（`lesson_exists`），同一条反馈提炼两次不会重复入库，返回 -1 表示已存在；
- **项目继承**：`project = str(fb.get("project") or "")`——反馈诞生于哪个项目，经验就归哪个项目。

### 项目隔离与通用经验

`LessonBook.all()` 委托给存储层查询——`lessons()` 的 project 语义是**两档**（store.py 注释）：

```python
        """查询经验。

        project 语义：
        - None → 全部；
        - 指定值 → 该项目 + 通用经验（project 为空）——通用经验对所有项目生效。
        """
```

即：**检索时"该项目 + 通用"，写入时"继承反馈的项目"**。为什么这样设计？——"`tests/` 里 `assert` 别报"这类经验对所有项目都成立（通用）；而"这个项目的分页器把 `except` 用得很刻意"只对那个项目生效（隔离）。经验如果全局串台，"越用越准"就变成"越用越怪"。**目前没有"提升为通用"的 CLI 入口**（通用经验 = project 为空，只能从对话记录里看到这个语义）——一个明确的行为边界，记在诚实清单的邻居位置。

### 注入文本：`few_shot_block` 的双重上限

```python
    def few_shot_block(self, limit: int = 6, project: str | None = None) -> str:
        """生成注入 prompt 的经验块（该项目 + 通用经验，控制条数与总字符数）。"""
        lessons = self.all(limit=limit, project=project)
        if not lessons:
            return ""
        lines: list[str] = []
        used = 0
        for lesson in lessons:
            prefix = "误报" if lesson.kind == LESSON_FALSE_POSITIVE else "漏报"
            line = f"- [{prefix}] {lesson.content}"
            if used + len(line) > MAX_BLOCK_CHARS:
                break
            lines.append(line)
            used += len(line)
        return "\n".join(lines)
```

**双重上限**：条数（默认 6）和总字符（`MAX_BLOCK_CHARS = 1200`）——先取最近的 6 条，逐条累加长度，超 1200 字符就停。**为什么要上限**：经验注入是"永久税"——每条经验都占每轮请求的 prompt 空间。如果放任积累，注入块会越来越大，最后挤压真正的审查上下文。**上限是给"经验"和"预算"划的边界。**

### 注入点：回到第 04 篇

链路闭环的最后一跳在 pipeline 装配段（第 04 篇 4.3 节读过）：

```python
                lessons_block = LessonBook(store).few_shot_block(
                    project=str(workspace)
                )
```

再经 `build_system_prompt(..., lessons_block=...)` 进入系统提示词的第四段：

```text
## 历史经验（来自反馈错题本，供参考）
以下模式曾被人工判定为误报或漏报，请在判断时注意：
- [误报] 【误报案例】utils.py 未使用 with 语句：该场景下刻意保持打开
- [漏报] 【漏报案例】pager.py 分页边界未校验：应报未报
```

注意两个措辞：**"供参考"**（经验是提示而非命令，不绑架模型判断）和**"曾被人工判定"**（标明来源的可信级别）。以及那层保险——第 04 篇看过的 `try/except`：错题本读取失败，审查以"无经验"模式照常进行。

**闭环的验收方式**（不靠感觉）：改了错题本 → 跑 `codesage eval --compare 修改前,修改后` → 看检出/误报有没有按预期变化（第 09 篇）。"注入错题本 → 误报率下降"如果有数据，才是可以写进简历和答辩的结论。

## 10.4 `codesage history`：全局存储的查询接口

错题本和反馈都躺在全局库里，`history` 是它的查询窗口：

```python
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
```

### 详情下钻：`--detail`

记录列表之外，`--detail <ID>` 把某次历史审查**完整重渲染**出来：

```python
        if detail is not None:
            payload = store.get_review_payload(detail)
            if payload is None:
                console.print(f"[red]找不到记录 #{detail}[/red]")
                raise typer.Exit(2)
            report = ReviewReport.from_dict(payload)
            console.print(f"[dim]历史报告 #{detail}（重新渲染）[/dim]")
            render_report(report, console)
            return
```

`payload` 就是落盘时 `json.dumps(report.to_dict())` 那一大坨（第 08 篇）——`ReviewReport.from_dict(payload)` 把它还原成对象（第 01 篇见过的类方法），再喂给同一个渲染器。**"落盘存全量 JSON"的回报在这里兑现**：任何一次历史审查都能原样回看，而不只是"分数、条数"这几个数字。

### `--project` 归一化：一个真实反馈的修复

查询项目记录时有个隐蔽的坑——写入端的 project 是**审查根目录**（单文件审查时取文件所在目录），而用户查询时很自然地把**文件路径**传进来："我审过 test.py，查一下"：

```python
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
```

修复的两种情形都考虑到了：**文件存在**（`is_file()`）→ 取父目录；**文件已删除但路径带后缀**（`not is_dir() and suffix`，比如 `old/test.py`）→ 也取父目录——查一个"已经删掉的文件所在项目"依然能命中。这是一次教科书式的"读写两端语义对齐"修复：**匹配失败不一定是没数据，也可能是两端的键定义不一致。**

### 展示层的两个体贴

列表里的目标路径会尽量相对化显示（当前目录下的目标显示相对路径）：

```python
def _display_target(target: str) -> str:
    """目标路径的相对化展示（在当前目录下时显示相对路径，更易读）。"""
```

反馈统计用中文标签（复用 `verdict_label`，第 10.2 节的映射表）。`--feedback` 的统计也按 project 过滤（`store.feedback_stats(project=project_key)`）——和记录列表保持同一视角。

## 10.5 Prompt 版本管理：已实现，未接线

环 2 的目标：**让 prompt 迭代有版本、有数据、可回滚**。`optim/prompts.py` 的注册表已经实现——版本号的设计很干净，**内容哈希前 8 位，内容相同即同版本**：

```python
    @staticmethod
    def version_of(template: str) -> str:
        """内容哈希（前 8 位）作为版本号——内容相同即同版本。

        非安全用途的短标识，usedforsecurity=False。
        """
        return hashlib.sha1(
            template.encode("utf-8"), usedforsecurity=False
        ).hexdigest()[:8]
```

`register()` 把模板写入 `prompts` 表（profile / version / template / note，写入幂等——同 profile+version 不重复插）。表结构在第 08 篇的五张表里已经见过。

**诚实标注**：全项目搜 `PromptRegistry`，引用只出现在 `optim/__init__.py` 的导出和它自己的定义里——**`register()` 在审查主链路中没有任何调用点**。也就是说：当前 `codesage eval --compare tagA,tagB` 能对比两次评测的指标，但结果里**还没有自动带上"本次用的 prompt 版本"**；改动 `BASE_SYSTEM_PROMPT` 后想对比，只能靠人记"这次改了什么"。环 2 缺的最后一段接线很清晰：pipeline 组装完系统提示词后登记版本、评测 summary 记录版本号、compare 展示版本差异。

**"基础设施就位、接线未到"** 是一种需要如实标注的中间态——它和"没实现"是两回事（表、哈希、幂等写入都在），和"已完成"也是两回事（主链路没用上）。

## 10.6 防过拟合纪律与小结

自优化最大的风险不是不好用，而是**好用到过拟合**：错题本里的案例最终把模型调成"只认识这几个项目"。三条纪律（`lessons.py` 头注释）对应三个机制：

| 纪律 | 机制 |
|---|---|
| 注入的经验有上限 | ≤6 条 & ≤1200 字符（10.3） |
| 效果必须过 A/B 评测（不能"感觉变好了"） | `eval --compare` 是唯一的验收方式（第 09 篇） |
| 评测集拆调优/验证 | 规划给定 7:3 拆分原则；当前数据集规模还小，拆分为手工流程 |

第四条隐含纪律是**项目隔离**（本轮刚加固的）：经验只在诞生地生效——这本身也是防过拟合的一种——防止 A 项目的特殊性污染 B 项目。

**诚实标注**：纪律本身明确（写在文档与模块注释里）；"数据集拆分"当前是手工流程（种子集只有 5 个案例，拆分没有统计意义）。它和环 2 接线一起，构成"数据集扩充到 25-30 个案例"待办的两翼。

**小结**：

- **环 1 已闭环**：feedback 三态采集（自动补上下文与项目归属）→ lessons 提炼（三态中只用两态）→ 注入系统提示词（双上限 + "供参考"措辞 + 注入失败兜底）；
- **项目隔离**：检索"该项目 + 通用"、写入继承反馈项目；history 的 `--project` 归一化修复了"读写两端键不一致"的真实问题；
- **history 两个能力**：`--detail` 全量重渲染（JSON payload 的回报）、`--feedback` 按项目统计；
- **环 2 半落地**：PromptRegistry 就位、接线未到（诚实标注）；
- **环 3 未实现**：参数自适应是 v2 方向；
- **纪律**：双上限、A/B 验收、防串台——**数据是唯一的守门人**。

支线二走完。最后一条支线是"工程实录"——44 倍性能优化的完整案例、119 个测试的地图、以及这两周内所有"用户反馈驱动的修复"汇总。

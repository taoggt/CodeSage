# 《CodeSage项目》——第 4 站 · 阶段三·准备：组装 Agent

> 📍 **主线进度**：起点 ✅ → 扫描 ✅ → 确定性分析 ✅ → **▶ 组装 Agent（本篇）** → 循环 → 工具 → 后勤 → 合并与输出

候选发现已经躺在 `det_findings` 里，接下来是阶段三的上半场：**把这些素材交给 Agent 之前，先把 Agent 的每个器官装配好**。这一篇把 `pipeline.py` 里那段装配代码逐行讲完——你会发现真正的"调用"只有最后一句 `loop.run(...)`，前面全是准备。准备好，是因为这个循环的每个依赖都是显式注入的（第 05 篇会看到为什么这样设计）。

## 4.1 装配段代码：五个对象、三种素材

先看全景代码（从"3. Agent 循环"注释到 `loop.run` 之前）：

```python
    # ---------------------------------------------------------- 3. Agent 循环
    llm_findings: list[Finding] = []
    dismissed: list[dict] = []
    loop_usage = TokenUsage()
    turns = 0
    stop_reason = ""
    compress_events = 0
    llm_used = False

    store = _open_store(settings.review.store_path)
    if options.use_llm and settings.llm_configured:
        llm_used = True
        client = LLMClient(settings.llm)
        router = ModelRouter(settings.llm)
        estimator = get_estimator()
        budget = TokenBudget(settings.budget)
        memory = WorkingMemory(active_window_turns=settings.review.active_window_turns)

        knowledge_base = default_knowledge_base()
        registry = build_default_tools(
            workspace=workspace,
            analyzers=analyzers,
            knowledge_base=knowledge_base,
            findings_collector=llm_findings,
            dismissed_collector=dismissed,
            read_limit=settings.review.tool_output_limit,
        )

        lessons_block = ""
        if store is not None:
            try:
                from codesage.optim.lessons import LessonBook

                lessons_block = LessonBook(store).few_shot_block(
                    project=str(workspace)
                )
            except Exception:  # noqa: BLE001 - 错题本读取失败不影响审查
                lessons_block = ""

        system_prompt = build_system_prompt(
            options.profile,
            workspace_summary=build_workspace_summary(workspace, files),
            lessons_block=lessons_block,
            budget_hint=(
                f"最多可执行 {settings.review.max_turns} 轮工具调用，请合理分配。"
            ),
        )
        brief = build_task_brief(
            target=str(target),
            files=file_inventory,
            candidates=det_findings,
            max_candidates=settings.review.max_candidates_in_prompt,
            changed_only=options.changed_only,
        )

        compressor = _make_compressor(client, router, budget)
        loop = AgentLoop(...)
        result = loop.run(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": brief},
            ]
        )
        loop_usage = result.usage
        turns = result.turns
        stop_reason = result.stop_reason
        compress_events = result.compress_events

    elif options.use_llm:
        stop_reason = "未配置 API Key，已降级为确定性分析"
```

先记住**条件分支的语义**：`options.use_llm and settings.llm_configured` 双条件——用户没加 `--no-llm`（`use_llm=True`）且配置里有 API Key，才走完整链路。否则落入 `elif`：发出"未配置 API Key，已降级为确定性分析"的结束原因，然后用已经算好的 `det_findings` 直接进入合并阶段。**注意降级是"整段跳过"而不是"报错"**——这是"没配置也能用"承诺的实现处。

装配出的对象和素材，一张表全览（后面三篇会反复回到这张表）：

| 名字 | 类型 | 作用 | 详讲 |
|---|---|---|---|
| `llm_findings` | `list[Finding]` | 模型提交的发现的收集列表（`submit_finding` 写入） | 第 06 篇 |
| `dismissed` | `list[dict]` | 驳回记录（`dismiss_candidate` 写入） | 第 06 篇 |
| `store` | `Store \| None` | 全局 SQLite（历史/反馈/错题本/prompts） | 第 08、10 篇 |
| `client` | `LLMClient` | LLM 通信（openai SDK 薄封装） | 第 07 篇 |
| `router` | `ModelRouter` | 按任务选模型（强/快） | 第 07 篇 |
| `estimator` | `TokenEstimator` | 估算上下文 token 量 | 第 07 篇 |
| `budget` | `TokenBudget` | 预算记账与熔断判定 | 第 07 篇 |
| `memory` | `WorkingMemory` | 三区记忆（活区 4 轮由配置定） | 第 07 篇 |
| `knowledge_base` | `KnowledgeBase` | 知识卡片（`query_knowledge` 工具的后端） | 第 06 篇 |
| `registry` | `ToolRegistry` | 工具注册表（7 个工具） | 第 06 篇 |
| `lessons_block` | `str` | 错题本注入文本（按项目隔离） | 第 10 篇 |
| `system_prompt` | `str` | 系统提示词（本 4.2 节） | 本篇 |
| `brief` | `str` | 任务简报（本 4.4 节） | 本篇 |
| `compressor` | 函数 | 压缩器（调快模型出摘要） | 第 07 篇 |
| `loop` | `AgentLoop` | 循环本体 | 第 05 篇 |

几个值得立刻展开的点：

- **两个收集器是"空列表传引用"**：`llm_findings` 和 `dismissed` 先建好，把**引用**传进 `build_default_tools`——工具执行时直接往这两个列表里写。这是 Python 最常见的"输出参数"模式（引用语义的正面用法，加餐 13 讲过的机制在这里干正事）。
- **`store = _open_store(settings.review.store_path)` 在分支外面**：无论走不走 LLM，存储都会尝试打开——因为错题本读取（LLM 分支内）和报告落盘（阶段五）都要用它。打开失败返回 `None`，两个使用点都有兜底。
- **错题本读取放在 `try` 里**，失败置空字符串继续——"错题本读取失败不影响审查"，第 11 篇的容错清单会再见面。
- **`_make_compressor(client, router, budget)` 传进去三个依赖**：压缩需要"调 LLM"（client）、"选快模型"（router）、"记账"（budget）——这个函数本身在第 07 篇拆，这里记住它返回的是**一个函数**。

## 4.2 系统提示词：五段式组装

`system_prompt` 由 `agent/profiles.py` 的 `build_system_prompt()` 组装：

```python
def build_system_prompt(
    profile_name: str = "full",
    *,
    workspace_summary: str = "",
    lessons_block: str = "",
    budget_hint: str = "",
) -> str:
    """组装系统提示词。

    参数：
    - workspace_summary: 项目背景（来自项目级记忆）
    - lessons_block: 错题本经验（自优化的注入点）
    - budget_hint: 资源提示（如"剩余可用轮次"）
    """
    parts: list[str] = [BASE_SYSTEM_PROMPT]

    profile = PROFILES.get(profile_name)
    if profile and profile.focus:
        parts.append(f"## 本次聚焦\n{profile.focus}")

    if workspace_summary:
        parts.append(f"## 项目背景\n{workspace_summary}")

    if lessons_block:
        parts.append(
            "## 历史经验（来自反馈错题本，供参考）\n"
            "以下模式曾被人工判定为误报或漏报，请在判断时注意：\n"
            f"{lessons_block}"
        )

    if budget_hint:
        parts.append(f"## 资源说明\n{budget_hint}")

    return "\n\n".join(parts)
```

五段按固定顺序拼接，各自的信息来源：

| 段 | 来源 | 何时存在 | 详讲 |
|---|---|---|---|
| 基础守则 `BASE_SYSTEM_PROMPT` | 代码常量 | 永远 | 本篇 |
| `## 本次聚焦` | `PROFILES[profile].focus` | profile ≠ full | 本篇 |
| `## 项目背景` | `build_workspace_summary()`（扫 import 识别技术栈） | 有文件时 | 4.3 节 |
| `## 历史经验` | 错题本 `LessonBook.few_shot_block()` | 有经验时 | 第 10 篇 |
| `## 资源说明` | 调用方拼的一句话 | 永远（本项目） | 本篇 |

### 基础守则全文（这是项目的"质量资产"）

`BASE_SYSTEM_PROMPT` 值得逐字读一遍——它不是随手写的提示词，七条守则每一条都对应一个真实失效模式：

```text
你是 CodeSage，一名资深 Python 代码审查专家（Agent）。

工作方式：通过工具读取真实代码，基于证据给出结论。
每条问题必须通过 submit_finding 工具以结构化方式提交；
不要在对话正文中用自然语言罗列问题（对话正文只用于简短汇报进度）。

## 审查守则（必须遵守）

1. **先复核候选**：初始上下文给出了确定性分析的候选发现，逐一读取相关代码核实：
   - **属实** → 用 submit_finding 提交你的版本（含结合代码上下文的修复建议）。
     去重时你的版本优先于工具原始版本——提交后报告中展示的将是你的分析；
     不提交则报告只保留工具的通用提示（如"请查阅文档"，缺乏具体修复方案）。
   - **误报** → 用 dismiss_candidate 驳回（会从最终报告中移除），
     不要让已核实为误报的候选继续留在报告里。
2. **补充独立发现**：重点找规则抓不到的问题——逻辑错误、
   边界条件（空值/None/越界/空集合）、资源泄漏、异常路径、
   并发与共享状态污染。
3. **证据要求**：每条发现必须引用具体文件与行号，行号以 read_file
   返回的编号为准，禁止臆测未读过的代码。
4. **宁可漏报，不可误报**：证据不足时不提交——误报会直接降低
   工具的可信度。不确定的判断可以通过 reading 更多上下文来确认。
5. **建议必须可执行**：给出具体改法（改后代码或明确的操作），
   禁止"建议优化性能"这类空话。
6. **效率**：大文件分段读取（read_file 指定行号范围）；
   定位符号用 search_code / list_symbols，不要通读整库。
7. **尊重测试代码惯例**：`tests/` 目录与 `test_*.py` 文件中，
   大量使用 assert、辅助函数不写 docstring、fixture 硬编码临时路径
   等属于测试领域的标准做法——不要提交此类发现，只报告
   测试代码中真正的逻辑错误（如断言写反、mock 失效）。

## 审查维度清单（提交前自检覆盖情况）

- 代码质量：命名、风格一致性、注释与 docstring 完整性
- 潜在 Bug：逻辑错误、边界条件、异常处理、资源管理
- 性能：算法复杂度、循环内重复计算、数据结构选择
- 安全：注入（SQL / 命令）、反序列化、硬编码密钥、路径处理
- 最佳实践：Python 惯用法（Pythonic）、设计模式适用性（切忌过度设计）
```

七条守则的"守什么"对照表：

| 守则 | 防的失效模式 | 对应机制 |
|---|---|---|
| 1. 先复核候选 | 模型无视候选清单自说自话；驳回后候选还留在报告里 | 两个工具通道 + 合并阶段的驳回过滤（第 06、08 篇） |
| 2. 补充独立发现 | 只做复核、浪费语义推理能力 | 任务简报的"独立审查"段 |
| 3. 证据要求 | 行号幻觉（报没读过的问题） | `read_file` 带行号；`submit_finding` 强制 file:line |
| 4. 宁可漏报 | 误报淹没有效信息（208 条事故的教训） | 复核机制 + 置信度字段 |
| 5. 建议可执行 | "建议优化性能"式空话 | `suggestion` 字段 + 报告展示 |
| 6. 效率 | 通读整库烧上下文 | `read_file` 分段 / `search_code` / `list_symbols` |
| 7. 测试惯例 | 测试代码噪音 | 与 `is_test_file` 豁免互为补充（第 03 篇） |

守则 1 是本轮更新改写过的——旧版本只说"属实则重新提交、误报则忽略"，现在把**两条通道的后果**讲清楚（"提交后报告中展示的将是你的分析；不提交则报告只保留工具的通用提示"）。**提示词也需要迭代：哪条规则模型执行得不到位，就把"为什么"写进规则里**。

> 你可能注意到守则 4 里有个英文词 "reading"（"通过 reading 更多上下文来确认"）。这是源码里的原样——第 00 篇说过，注释和提示词里的原文一律不改。就当它是"代码考古"的一个小标本。

### Profile：同一个循环，三种聚焦

```python
PROFILES: dict[str, Profile] = {
    "full": Profile(name="full", title="全面审查（五维度）", focus=""),
    "security": Profile(
        name="security",
        title="安全聚焦",
        focus=(
            "本次审查聚焦安全维度：注入类（SQL / 命令 / 模板）、"
            "反序列化与 eval、硬编码凭据、路径穿越、SSRF、"
            "不安全随机数。其它维度仅在有严重问题时顺带提及。"
        ),
    ),
    "performance": Profile(
        name="performance",
        title="性能聚焦",
        focus=(
            "本次审查聚焦性能维度：算法复杂度（O(n²) 及以上）、"
            "循环内重复计算 / 重复属性查找、错误的数据结构选择、"
            "不必要的物化与拷贝、I/O 与网络调用模式。"
            "其它维度仅在有严重问题时顺带提及。"
        ),
    ),
}
```

模块注释有一句值得记住的立场：

> Profile 只负责"聚焦维度"的差异化（full / security / performance），
> 避免为多角色而多角色——实现上是同一个循环 + 不同 Prompt。

**没有为"多角色"引入多 Agent 架构**，角色降维成 prompt 里的一个参数段——复杂度归零，代价是不同角色不能并行。对单目标的 CLI 场景，这笔账划得来。

> ### 【Python 加餐 15】装饰器（上）：`@tool` 与 Java 注解的本质差异 ★
>
> 你在 `@property`、`@dataclass`、`@tool`、`@classmethod` 里已经见了一串 `@` 开头的东西。现在把本质讲透——**这是 Java 背景最容易误解的语法**。
>
> **先破除误解：Python 装饰器不是注解。** Java 注解（`@Override`、`@Data`）是**元数据**——躺在类文件里，等着框架用反射去读。Python 装饰器是**运行时的函数替换**——`@tool` 这一行的真实含义是：
>
> ```python
> # 这个写法……
> @tool
> def read_file(path: str) -> str: ...
>
> # ……完全等价于这样写：
> def read_file(path: str) -> str: ...
> read_file = tool(read_file)
> ```
>
> **装饰器就是一个"接收函数、返回函数"的函数**，`@` 只是语法糖。所以：
>
> - 它可以**包裹行为**（原函数前后加逻辑、记录日志、计时）——Java 注解做不到，得靠 AOP 或代理；
> - 它可以在返回前**检查、替换甚至注册**原函数；
> - 它从下往上执行（多个装饰器叠放，离函数近的先执行）。
>
> 本项目里的 `@tool` 恰好是"最像注解"的那种用法——**只挂元数据、不包装任何逻辑**：
>
> ```python
> def tool(func=None, *, name=None, description=None):
>     """装饰器：把函数标记为工具（只挂元数据，不产生全局副作用）。"""
>     def decorate(f):
>         f._is_codesage_tool = True
>         f._tool_name = name or f.__name__
>         if description is not None:
>             f._tool_description = description
>         return f
>     if func is not None:
>         return decorate(func)
>     return decorate
> ```
>
> `f._is_codesage_tool = True`——**给函数对象挂一个属性**。Java 里方法是类的一部分、不能这样挂东西；Python 里函数是普通对象（一等公民），挂属性天经地义。这些属性在"注册进 ToolRegistry"时被 `getattr` 读到（4.5 节）。
>
> 签名的两种形态也值得注意：`tool(func=None, *, name=...)` 既支持 `@tool`（直接装饰），也支持 `@tool(name="submit_finding")`（带参数装饰）——**用默认值和"两阶段返回"做到两种用法一个函数**（收到函数就直接处理；没收到就返回 `decorate` 等待下一个调用）。Java 想实现同样效果得写两个重载入口，Python 用一个函数搞定。
>
> 最后预告：装饰器的"包裹行为"形态在标准库里到处都是（`@property` 把方法变属性、`@classmethod` 把函数变类方法）——第 06 篇【Python 加餐 22】会把项目里出现的三种形态（`@tool` / `@dataclass` / `@property`）摆在一起对比。

### 资源说明一段

`budget_hint` 是调用方拼的一句话：

```python
            budget_hint=(
                f"最多可执行 {settings.review.max_turns} 轮工具调用，请合理分配。"
            ),
```

**把预算明明白白告诉模型**——它会据此安排"先读什么、读多深"。这不是寒暄，是资源约束的传达：模型知道额度只有 12 轮，就不会在第一轮漫无目的地啃整个文件。

## 4.3 项目背景与错题本：进提示词的两段"记忆"

### 项目背景：零 Token 的确定性分析

`build_workspace_summary(workspace, files)` 在 `memory/project.py`——模块注释：

```python
"""项目级记忆：项目画像（供 system prompt 的"项目背景"段）。

零 Token 的确定性分析：读取 import 语句识别技术栈，
扫描目录结构生成概览——让模型知道自己在审查什么项目。
"""
```

它做三件事：

1. **扫 import 识别技术栈**：`FRAMEWORK_HINTS` 是张"顶层包名 → 展示名"的映射表（fastapi→FastAPI、pydantic→Pydantic、pytest→pytest……共 22 项），对前 60 个文件做 AST 解析，收集命中的框架；
2. **目录概览**：取文件相对路径的第一段（`src/`、`tests/`……）最多展示 12 个；
3. **组装文本**：

```python
    lines = [
        f"- 语言：Python（{len(files)} 个文件，约 {total_lines} 行）",
        f"- 顶层结构：{structure or './'}",
    ]
    if deps:
        lines.append(f"- 疑似技术栈：{', '.join(deps)}")
    lines.append(
        "- 审查时请结合项目技术栈给出符合该生态惯例的建议"
        "（如 Web 项目关注请求处理与注入、数据项目关注向量化与内存）。"
    )
    return "\n".join(lines)
```

注入实例（审查本项目自己时）：

```text
## 项目背景
- 语言：Python（62 个文件，约 6637 行）
- 顶层结构：./, src/
- 疑似技术栈：Pydantic, pytest, Typer
- 审查时请结合项目技术栈给出符合该生态惯例的建议（如 Web 项目关注请求处理与注入、数据项目关注向量化与内存）。
```

**这就是"项目级记忆"的全部**——不神秘，就是三段零成本的确定性分析。成本控制在这里体现的最直白：没启动 LLM，先确定性地把"这是个什么项目"告诉它。

### 错题本：一段按项目隔离的经验

```python
        lessons_block = ""
        if store is not None:
            try:
                from codesage.optim.lessons import LessonBook

                lessons_block = LessonBook(store).few_shot_block(
                    project=str(workspace)
                )
            except Exception:  # noqa: BLE001 - 错题本读取失败不影响审查
                lessons_block = ""
```

`few_shot_block(project=str(workspace))` 用工作区绝对路径作为"项目键"取经验——**错题本按项目隔离**（同一个项目的经验只注入给它自己，另加通用经验）。这套机制怎么积累、怎么注入的完整闭环在第 10 篇；这里只记住：`lessons_block` 会作为 `## 历史经验` 段拼进系统提示词。

## 4.4 任务简报：token 控制的第一道闸门

系统提示词的"另一半"是初始 user 消息——任务简报，由 `agent/context.py` 的 `build_task_brief()` 生成。模块注释：

```python
"""初始上下文组装：把扫描结果 + 候选发现变成任务简报。

Token 控制的第一道闸门：
- 文件清单只给路径与行数（不给内容，让模型按需 read_file）；
- 候选发现按严重级排序并限制条数（max_candidates）；
- 分块策略提示（大文件怎么读）。
"""
```

### 简报全文模板

```python
    return f"""## 审查任务

目标：{target}（Python 文件 {len(files)} 个）
{scope_note}

## 文件清单（相对路径 | 行数）

{build_file_inventory(files)}

## 确定性分析候选发现（共 {len(candidates)} 条，需复核）

{build_candidates_block(candidates, max_candidates)}

## 建议的工作流程

1. 复核候选发现：读取相关代码确认属实性（属实→submit_finding 重新提交并附修复建议；误报→忽略）。
2. 独立审查文件：优先审查未被候选覆盖的文件与核心逻辑（工具、公共接口优先）。
3. 全部完成后，输出一段简短总结（不再调用工具）结束任务。
"""
```

四段各司其职：

**第一段 · 任务**：`scope_note` 是两选一的句子——`--changed` 时是"本次为**增量审查**：只关注 git 变更涉及的文件与行区间附近的代码。"，否则"本次为**全量审查**。"。

**第二段 · 文件清单**：只给"路径 | 行数"，**不给内容**：

```python
def build_file_inventory(files: list[tuple[str, int]]) -> str:
    """文件清单（路径 | 行数），限制展示数量避免占满上下文。"""
    if not files:
        return "（无文件）"
    lines = [f"- {path} | {lines} 行" for path, lines in files]
    return "\n".join(lines)
```

62 个文件的完整内容可能几十万 token，但清单只有几千 token。模型拿着地图按需 `read_file`——**"模型自主决定看什么"的入口设计**。

**第三段 · 候选发现**：这是最见功力的一段：

```python
def build_candidates_block(
    candidates: list[Finding],
    max_candidates: int = 40,
) -> str:
    """候选发现清单（供模型复核）。"""
    if not candidates:
        return "（无候选发现——请独立审查全部文件）"

    order = {"critical": 0, "major": 1, "minor": 2, "info": 3}
    sorted_candidates = sorted(
        candidates,
        key=lambda f: (order[f.severity.value], f.file, f.line_start),
    )
    shown = sorted_candidates[:max_candidates]
    lines = []
    for idx, f in enumerate(shown, start=1):
        lines.append(
            f"{idx}. [{f.severity.label}][{f.category.value}] "
            f"{f.file}:{f.line_start} [{f.rule_id or f.source.value}] {f.title}"
        )
    if len(sorted_candidates) > max_candidates:
        lines.append(
            f"...（另有 {len(sorted_candidates) - max_candidates} 条低优先级候选未列出）"
        )
    return "\n".join(lines)
```

- **排序规则**：严重级降序 → 文件 → 行号。模型从上往下读，最先看到最该复核的；
- **截断规则**：最多 `max_candidates_in_prompt` 条（默认 40）。超出的**明确告知**："另有 N 条低优先级候选未列出"——模型知道信息不全，可以自己去搜；
- **每行格式**：`序号. [P1][bug] file.py:14 [CG002] 裸 except（except:）捕获全部异常`—— 一眼可读，且每条都能对应到"复核 → 提交/驳回"的动作。

第一条特殊情况也处理了：**没有候选时**返回"（无候选发现——请独立审查全部文件）"——把"空"变成明确的指示。

**第四段 · 工作流程**：三句话就是整个循环的宏观剧本——复核 → 独立审查 → 总结收尾。最后一句"输出简短总结（不再调用工具）"直接对应循环的 `finished` 退出路径（第 05 篇）：**prompt 里写的结束方式，和代码里的退出条件是对齐的**。

## 4.5 工具注册：`build_default_tools`

最后一个装配件是工具注册表：

```python
def build_default_tools(
    *,
    workspace: Path,
    analyzers: list[Any],
    knowledge_base: Any,
    findings_collector: list[Finding],
    dismissed_collector: list[dict] | None = None,
    read_limit: int = 500,
) -> ToolRegistry:
    """组装默认工具集。"""
    registry = ToolRegistry()
    registry.register(make_read_file_tool(workspace, limit=read_limit))
    registry.register(make_search_code_tool(workspace))
    registry.register(make_list_symbols_tool(workspace))
    if analyzers:
        registry.register(make_run_analyzer_tool(analyzers, workspace))
    if knowledge_base is not None:
        registry.register(make_query_knowledge_tool(knowledge_base))
    registry.register(make_submit_finding_tool(findings_collector, workspace))
    if dismissed_collector is not None:
        registry.register(make_dismiss_candidate_tool(dismissed_collector))
    return registry
```

七个工具在下一站（第 06 篇）逐个拆。这里先把**装配模式**讲清楚——三条规律：

1. **每个工具由 `make_xxx_tool(依赖...)` 工厂创建**，依赖（工作区、分析器、收集器）通过参数传入、在函数内部被"记住"；
2. **条件注册**：`analyzers` 为空不注册 `run_analyzer`、没有知识库不注册 `query_knowledge`——**给不了的能力就别让模型看到**（工具的 schema 会出现在每轮请求里，模型只应该看见真实可用的东西）；
3. **模型不直接"看见"依赖**：模型调用 `read_file(path="a.py")` 只传 `path`；工作区路径、行数上限都是工厂预置的。

> ### 【Python 加餐 16】闭包与函数工厂（Java 对照）
>
> 看一个真实工具的完整创建过程（`read_file` 精简版）：
>
> ```python
> def make_read_file_tool(workspace: Path, limit: int = 500):
>     """创建 read_file 工具。
>
>     limit 是单次返回的最大行数——从源头控制上下文增长，
>     防止模型一次读取就把上下文撑爆。
>     """
>     root = Path(workspace).resolve()
>
>     @tool(name="read_file")
>     def read_file(path: str, start_line: int = 1, end_line: int = 0) -> str:
>         """读取工作区内文件的内容（带行号，单次最多返回若干行）。"""
>         target = _resolve_inside(root, path)   # ← root 从哪来？外层函数！
>         ...
>         end = min(start + limit - 1, total)     # ← limit 也是外层函数的参数
>     return read_file
> ```
>
> `read_file` 的签名里只有 `path/start_line/end_line`——**模型只看到这三个参数**。但函数体里用了 `root` 和 `limit`——它们来自外层 `make_read_file_tool` 的参数。函数"记住"了定义它时的外层变量，这就是**闭包（closure）**。
>
> Java 对照：Java 的 lambda 也能捕获外层局部变量，但要求变量 `final/effectively final`；Python 闭包捕获的是**变量本身**（可以改，用 `nonlocal` 声明——第 05 篇【Python 加餐 20】讲作用域时展开）。写成工厂函数而不是类，是 Python 的常见偏好：一个"携带配置的行为"用闭包大约 10 行，写成类要 `__init__` + 方法 + 实例，30 行起。
>
> 为什么这个模式对本项目至关重要？回到模块头注释：
>
> ```python
> """工具集：Agent 可调用的能力。
>
> 设计模式：工厂函数 + 依赖注入。
> 每个工具通过 make_xxx_tool(...) 创建，把工作区路径、分析器、知识库、
> 发现收集器等依赖闭包进去，工具函数本身保持"纯函数式"接口
> （只接收模型给的参数），便于测试与复用。
> """
> ```
>
> 三个好处：**模型侧接口干净**（只有业务参数）；**测试容易**（工厂传临时目录/假收集器，如 `tests/test_tools.py` 里 `make_submit_finding_tool(collector, Path("."))`）；**能力可组合**（同一次测试可以造多个隔离的工具实例）。

> ### 【Python 加餐 17】反射与鸭子类型：schema 是怎么自动生成的（Java 对照）
>
> 每个工具都要给模型一份 JSON Schema。手写会随签名变化而失真，项目的做法是**反射生成**（`agent/registry.py` 的 `register()`）：
>
> ```python
>         properties: dict[str, Any] = {}
>         required: list[str] = []
>         signature = inspect.signature(func)
>         for pname, param in signature.parameters.items():
>             if pname in ("self", "cls"):
>                 continue
>             schema = _annotation_to_schema(param.annotation)
>             schema["description"] = param_docs.get(pname, pname)
>             properties[pname] = schema
>             if param.default is inspect.Parameter.empty:
>                 required.append(pname)
> ```
>
> 逐单位拆：
>
> - `inspect.signature(func)`——**运行时读取函数的参数表**：名字、类型注解（`param.annotation`）、默认值（`param.default`）。≈ Java 的 `method.getParameters()`，但不需要先拿到 `Class`、不用处理泛型擦除，函数对象直接就能问；
> - `param.default is inspect.Parameter.empty`——判断"有没有默认值"。**注意这里用 `is` 而不是 `==`**：`inspect.Parameter.empty` 是个哨兵对象（sentinel），语义是"同一个身份"，`is` 精确且快速（第 05 篇加餐细讲 `is` 与 `==`）；
> - 有默认值 → 不是必填；没有 → 进 `required` 列表。
>
> **鸭子类型**在哪？整个机制不要求工具函数继承任何基类、实现任何接口——只要有签名、有 docstring、被 `@tool` 挂了名字，注册表就能处理它。"如果它走起来像鸭子，就叫它鸭子"：`ToolRegistry.register()` 关心的是"你能不能被 `inspect` 到"，而不是"你是什么类型"。Java 要做同样的事必须定义 `Tool` 接口、写一个包装类——类型系统的强制性在这里让位给了灵活性。**代价是"错到运行期才发现"**——项目靠单测（`test_registry.py`）来补这层纪律。

## 4.6 组装完成：交给循环的依赖清单

最后看一眼 `AgentLoop` 的构造调用——十一个参数，全部是"显式注入"：

```python
        loop = AgentLoop(
            client=client,
            registry=registry,
            memory=memory,
            budget=budget,
            estimator=estimator,
            model=router.model_for(TaskKind.REVIEW),
            policy=StopPolicy(
                max_turns=options.max_turns or settings.review.max_turns
            ),
            config=LoopConfig(
                max_turns=options.max_turns or settings.review.max_turns,
            ),
            compressor=compressor,
            findings_counter=lambda: len(llm_findings),
            on_event=options.on_event,
        )
```

| 参数 | 值 / 来源 | 备注 |
|---|---|---|
| `client` | 刚装配的 `LLMClient` | 第 07 篇 |
| `registry` | 7 个工具的注册表 | 第 06 篇 |
| `memory` | 三区记忆 | 第 07 篇 |
| `budget` / `estimator` | 预算与估算器 | 第 07 篇 |
| `model` | `router.model_for(TaskKind.REVIEW)` = 强模型 | 第 07 篇 |
| `policy` | `StopPolicy(max_turns=...)` | 第 05 篇 |
| `config` | `LoopConfig(max_turns=...)` | 第 05 篇（下一节有个诚实标注） |
| `compressor` | 压缩器（快模型 + 零温度） | 第 07 篇 |
| `findings_counter` | `lambda: len(llm_findings)` | 第 05 篇（无进展检测的原料） |
| `on_event` | CLI 进度打印器 | 第 05 篇 |

`options.max_turns or settings.review.max_turns` 这个表达式出现两次：命令行 `--max-turns` 给了用命令行的，没给用配置的——**优先级在代码里显式写出来**，不做隐式合并。

> **诚实标注（此处顺带）**：`LoopConfig.max_turns` 这个字段被赋值了，但循环实际读取轮次上限用的是 `StopPolicy.max_turns`——`LoopConfig` 里真正被读取的只有 `max_tool_result_chars`。这个"定义了但没生效"的字段记在第 12 篇诚实清单里（`LoopConfig` 的默认值都是 12，两处恰好一致，所以没有实际影响，但字段冗余是事实）。

## 4.7 小结与下一站

- **装配段**：双条件决定是否走 LLM；`llm_findings`/`dismissed` 两个收集器以引用传入工具；`store` 全局打开、失败返回 None；错题本失败静默置空。
- **系统提示词五段式**：基础守则 + 聚焦 + 项目背景 + 历史经验 + 资源说明；`BASE_SYSTEM_PROMPT` 全文与七条守则的对照表在手；Profile 只改 prompt 不改代码路径。
- **任务简报四段式**：任务（全量/增量）→ 文件清单（只有地图）→ 候选发现（排序 + 截断 + 明确告知）→ 工作流程（提示词与退出条件对齐）。
- **工具注册**："工厂 + 闭包"注入依赖；条件注册"给不了的能力不让模型看见"。
- **交给循环的十一个依赖**全部显式注入；`options.max_turns or settings.review.max_turns` 的优先级写法记住。
- **Python 加餐**：装饰器本质（★，vs Java 注解）、闭包与函数工厂、反射与鸭子类型。

下一站进入**阶段三 · 运行**——`loop.run([{"role": "system", ...}, {"role": "user", ...}])` 这一行是整个项目的心脏。它开始转起来之后，每一条候选发现、每一次工具调用、每一次记忆压缩都将被看清。

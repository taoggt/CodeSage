# 《CodeSage项目》——第 5 站 · 阶段三·运行：ReAct 循环

> 📍 **主线进度**：起点 ✅ → 扫描 ✅ → 确定性分析 ✅ → 组装 ✅ → **▶ ReAct 循环（本篇）** → 工具 → 后勤 → 合并与输出

`loop.run(...)` 这一行是整个项目的心脏。它是"自研 Agent 内核"的实体——没有 LangChain、没有 LangGraph，227 行（其中核心骨架约 150 行）讲完了 Agent 该怎么活。这一篇逐段精读整个循环：每一轮的五步、四种死法、错误往哪儿去、以及循环为什么"不认识"它的任何一个器官。

## 5.1 循环全景：五步骨架与四个出口

`agent/loop.py` 的模块头把骨架先画出来了：

```text
循环骨架（约 150 行的核心）：

    while True:
        1. 估计当前上下文 token 量 → 检查预算状态（OK/SOFT/HARD/ABORT）
        2. ABORT → 熔断退出（输出已有发现）
           HARD  → 调用前强制压缩
        3. 调用 LLM（带工具 schema）
        4. 无 tool_calls → 模型认为完成，正常结束
           有 tool_calls → 逐个执行并回填结果（role=tool）
        5. 回合末：轮次/进展检查；SOFT → 回合边界压缩
```

另有三条设计要点（同样来自文件头）：

> - 循环不认识"压缩细节"与"发现收集"（依赖注入）——关注点分离；
> - 工具错误不抛出，以文本回填让模型自我纠正；
> - 每一步都通过 on_event 钩子可观测（CLI 进度、调试、评测 trace）。

把骨架展开成"一轮的生命周期"：

```text
run(initial_messages)
  │  freeze：系统提示 + 任务简报进入冻结区（此后不改动）
  ▼
┌─ 每一轮 ─────────────────────────────────────────────┐
│  ① 预算检查    估算当前上下文 tokens → OK/SOFT/HARD/ABORT │
│                 ABORT → 熔断（出口③）                    │
│                 HARD  → 调用前强制压缩                   │
│  ② LLM 调用    client.chat(消息序列 + 工具 schema)      │
│                 LLMError → 错误（出口④）                │
│  ③ 判定分支    无 tool_calls → finished（出口①）         │
│                 有 tool_calls → 逐个执行工具、回填结果     │
│  ④ 回合末      记进展 → 停止策略检查 → 到期（出口②）      │
│                 估算 tokens → SOFT/HARD → 回合边界压缩    │
└──────────────────────────────────────────────────────┘
  ▼
收尾：统计归集到 AgentResult → 返回
```

**四个出口**（贯穿全文的参照系）与对应的 `AgentResult.status`：

| 出口 | 状态值 | 触发条件 | 报告里的结束语 |
|---|---|---|---|
| ① 模型主动结束 | `finished` | 响应无 tool_calls | 模型主动结束 |
| ② 策略停机 | `stopped` | 轮次上限 / 连续无进展 | 达到轮次上限（12 轮） / 连续 2 轮无新增发现… |
| ③ 预算熔断 | `aborted` | 使用率 ≥ 95% | Token 预算熔断（使用率 ≥ 95%），输出已有发现 |
| ④ 调用失败 | `error` | LLM 抛 `LLMError` | LLM 调用失败：… |

**为什么用原生 function calling，而不是"文本解析 ReAct"？** 经典 ReAct 实现让模型输出 `Thought:/Action:/Action Input:` 文本，程序用正则解析——模型换一种写法就崩。CodeSage 走协议路线：请求里带 `tools` schema，响应里读结构化的 `tool_calls`。对照表：

| 维度 | 文本解析 ReAct | 原生 function calling |
|---|---|---|
| 解析 | 正则匹配，格式一飘就失败 | 协议层保证结构化，无解析器 |
| 参数 | 自由文本，需自行约定格式 | JSON Schema 校验 |
| 可靠性 | 低（prompt 改动就可能破坏） | 高（模型侧原生支持） |
| 排错 | 分不清模型错还是解析错 | 每一步都是明确的数据结构 |

原则和整个项目一致：**协议层"标准且无优化空间"的能力用现成的，核心控制逻辑（循环、压缩时机）自研。** 选 DeepSeek 作为默认模型的前提之一，就是它稳定支持 function calling。

## 5.2 `run()` 逐段精读

### 开头：冻结与建账

```python
    def run(self, initial_messages: list[dict[str, Any]]) -> AgentResult:
        """执行循环直到结束。

        initial_messages 会被冻结（系统提示 + 任务简报），循环内不再改动。
        """
        self._memory.freeze(initial_messages)
        state = AgentState()
        result = AgentResult()
        self._emit("start", {"tools": self._registry.names()})
```

- **`freeze`**：把 `[system 提示词, user 任务简报]` 两条消息放进记忆的冻结区——整个循环期间不再改动。三区模型的细节在第 07 篇；这里记住"冻结 = 前缀稳定 = 有机会命中模型的 prompt 缓存"。
- **`AgentState`**（循环状态）与 **`AgentResult`**（结果账本）在 5.4、5.3 分开讲。
- **`start` 事件**带上工具清单——出问题时第一眼能确认"这次审查开了哪些工具"。

### 每一轮 · 第 1 步：预算检查

```python
        while True:
            # ---- 1. 预算检查（调用前）
            context_tokens = self._estimate_context()
            budget_state = self._budget.state(context_tokens)
            self._emit(
                "context",
                {
                    "turn": state.turn,
                    "tokens": context_tokens,
                    "ratio": round(self._budget.ratio(context_tokens), 3),
                    "state": budget_state.value,
                    "messages": len(self._memory.build()),
                },
            )

            if budget_state == BudgetState.ABORT:
                result.status = "aborted"
                result.stop_reason = (
                    f"Token 预算熔断（使用率 ≥ {int(self._budget_ratio_abort * 100)}%），"
                    "输出已有发现"
                )
                break
            if budget_state == BudgetState.HARD:
                self._compress(context_tokens)
```

三个私有辅助先认个脸（都在本文件）：

- `self._estimate_context()` → `self._estimator.estimate_messages(self._memory.build())`——把"当前组装出来的完整消息序列"估一个 token 数；
- `self._budget.state(tokens)` → 返回 `OK / SOFT / HARD / ABORT` 四态之一（阈值来自配置：60%/80%/95%）；
- `self._budget_ratio_abort` → 一个只读属性，转发 `self._budget.abort_ratio`——熔断百分比用于拼提示文案。

`context` 事件的载荷是全项目观测性最强的数据点，逐字段：

| 字段 | 含义 | 用途 |
|---|---|---|
| `turn` | 当前轮次 | 对齐进度 |
| `tokens` | 估算的上下文 token 数 | 预算诊断 |
| `ratio` | 使用率（保留 3 位小数） | 阈值诊断 |
| `state` | 状态机值（ok/soft/hard/abort） | CLI 黄字提示的条件 |
| `messages` | 当前消息条数 | 压缩效果对照 |

**分支语义**：`ABORT` 直接熔断（出口③）——"宁可输出不完整的报告，也不让下一次调用因超限失败把整场审查搞崩"（第 07 篇细说这笔账）；`HARD` 则在**调用前**强制压缩——不压缩这一轮可能根本塞不下。

### 每一轮 · 第 2 步：LLM 调用

```python
            # ---- 2. LLM 调用
            try:
                response = self._client.chat(
                    self._memory.build(),
                    tools=self._registry.schemas(),
                    model=self._model,
                )
            except LLMError as exc:
                result.status = "error"
                result.stop_reason = f"LLM 调用失败：{exc}"
                break
```

`client.chat()` 的三个参数就是一次请求的全部：**组装好的消息序列**（`memory.build()`：冻结区 + 摘要 + 活跃区）、**工具 schema 列表**（`registry.schemas()`：7 个工具的 JSON Schema）、**模型名**（装配时由 Router 决定的强模型）。返回值是自定义的 `LLMResponse`——带 `content` / `tool_calls` / `usage`。

异常处理只认一种：`LLMError`（客户端把所有底层异常统一包装成它，第 07 篇）。走到这里就是出口④——`error` 状态，前面几轮的发现仍能进报告（它们已经写在收集器里了）。

### 每一轮 · 第 3 步 a：模型说"完成了"

```python
            # ---- 3a. 无工具调用 = 模型认为任务完成
            if not response.has_tool_calls:
                self._memory.append(
                    {"role": "assistant", "content": response.content or ""}
                )
                result.status = "finished"
                result.stop_reason = "模型主动结束"
                result.final_message = response.content or ""
                self._emit("finish", {"message": response.content[:200]})
                break
```

`response.has_tool_calls` 是 `LLMResponse` 上的一个 `@property`（`bool(self.tool_calls)`）。没有工具调用 = 模型给出的最终消息（任务简报的最后一条指示就是"输出一段简短总结（不再调用工具）结束任务"）——写进记忆（保持对话完整）、记状态、发 `finish` 事件（预览 200 字符）、退出循环。

> ### 【Python 加餐 18】真值判断与 None（Java 对照）
>
> 注意刚才的写法：`if not response.has_tool_calls:`——`has_tool_calls` 已经是 bool，`not` 翻转即可。但在 Python 里，**很多东西不写比较也能进 if**：
>
> ```python
> if history:          # 空列表 → False，非空 → True（第 5.4 节的停止策略里就有这行）
> if text:             # 空字符串 → False
> if response.content or ""   # 空字符串/None → 取 ""
> if budget_state == BudgetState.ABORT:   # 枚举比较照常显式写
> ```
>
> **规则：下列值在布尔语境里都是 False**——`False`、`None`、数字 `0`、空字符串 `""`、空容器（`[]`、`{}`、`()`、空 `set()`）。其余为 True。这叫"真值测试"（truthiness），靠的是对象实现 `__bool__`（或退而求其次 `__len__`）。
>
> 和 Java 最大的差异：Java 的 `if` 只吃 `boolean`，`if (list)` 编译不过；Python 把"空 = 假"直接内置成语言语义。好处是 `if not candidates:` 这类判断极其干净；代价是**一个空的合法对象和"没拿到"难以区分**——所以 Python 程序员有一条老规矩：**该用 `is None` 的地方别偷懒用 `not x`**（`x = 0` 或 `x = ""` 是完全合法的值，`not x` 会误判！）。
>
> **`None` 与比较**：`None` 是单例（全程序只有一个 None 对象），判断"是不是 None"用 `is`：
>
> ```python
> if target is None: ...          # ✅ 正确
> if target is not None: ...      # ✅
> if target == None: ...          # ⚠️ 能跑但不地道——CG004 规则就是抓这个的
> ```
>
> `==` 可以被自定义类型重载（比如某个对象愿意和 None "相等"），`is` 永远是身份判断。**记住分工：与 None 比较用 `is`，与数字/字符串比较用 `==`。** 项目里 `is None` 出现了几十次（`if finding is None: continue` 之类），一次 `== None` 都找不着——因为 CG004 自己盯着。
>
> **`or` 的"取默认值"惯用法**：`settings or load_settings()`、`response.content or ""`——`a or b` 的语义是"a 为真就取 a，否则取 b"（短路求值，返回的是**操作数本身**而不是布尔值！）。这等价于 Java 的 `Optional.ofNullable(a).orElse(b)`，但顺手得多。三个警示：`x or []` 会把合法的"空列表"也换掉（`0 or 5` 得 5——如果 0 是合法值就出错了），需要区分"None 与空"时老老实实写 `if x is None`。

### 每一轮 · 第 3 步 b：逐个执行工具、回填结果

```python
            # ---- 3b. 有工具调用：回填 assistant 消息 → 逐个执行工具
            self._memory.append(assistant_message_with_tool_calls(response))
            call_names = [tc.name for tc in response.tool_calls]
            self._emit("tool_calls", {"turn": state.turn + 1, "names": call_names})

            for tc in response.tool_calls:
                output = self._registry.execute(
                    tc.name,
                    tc.arguments,
                    max_result_chars=self._config.max_tool_result_chars,
                )
                self._memory.append(tool_result_message(tc.id, output))
                state.tool_calls_total += 1
                self._emit(
                    "tool_result",
                    {"name": tc.name, "preview": output[:120].replace("\n", " ")},
                )
```

这一步是 function calling 协议的完整闭环，逐动作：

1. **先把 assistant 消息（含 `tool_calls`）写回记忆**——协议要求：下一轮请求里，`role=tool` 的结果必须能对应前面某条 assistant 消息中的 `tool_call_id`。顺序出错，下一轮 API 直接报协议错误。
2. **逐个执行**：`registry.execute(name, arguments, max_result_chars)`。`max_result_chars` 来自 `LoopConfig.max_tool_result_chars`（默认 6000）——**工具输出进场前的硬截断**，上下文管控的前哨。
3. **结果以 `role=tool` 回填**（`tool_result_message(tc.id, output)`）——注意带的是 `tc.id`（工具调用 ID），配对靠它。
4. **计数 + 事件**：`tool_calls_total` 累加；`tool_result` 事件里预览前 120 字符（`\n` replace 成空格，防止进度打印换行乱版）。

一次请求里模型可能同时要调多个工具（`response.tool_calls` 是列表）——这里的 `for` 循环把它们依次执行、依次回填。`execute()` 永不抛异常（第 06 篇细讲），所以这个循环不需要 try。

执行完这批工具回到 `while True` 顶部——**下一轮**：带着工具结果再问一次模型，这就是"ReAct"里 Act → Observe → 再 Thought 的转动方式。

### 每一轮 · 第 4 步：回合末的检查与软压缩

```python
            # ---- 4. 回合末：进展记录 + 停止检查 + 软阈值压缩
            state.turn += 1
            if self._findings_counter is not None:
                state.findings_history.append(self._findings_counter())

            should_stop, reason = self._policy.check(state)
            if should_stop:
                result.status = "stopped"
                result.stop_reason = reason
                break

            after_tokens = self._estimate_context()
            if self._budget.state(after_tokens) in (BudgetState.SOFT, BudgetState.HARD):
                self._compress(after_tokens)
```

四件事：

1. **`state.turn += 1`**——轮次计数（`+=` 对 int 是新对象，加餐 13 的老知识）。
2. **记进展**：`self._findings_counter()` 是装配时注入的 `lambda: len(llm_findings)`——每轮末把"当前累计发现数"追进 `findings_history`（比如 `[2, 5, 5, 5]`）。**循环不知道发现存在哪里，只知道"问一把、记下来"**——这是"循环不认识发现收集"的字面含义。
3. **停止检查**：`should_stop, reason = self._policy.check(state)`——注意**元组解包**：一个返回值拆成两个变量（加餐 19 细讲）。策略说停就停（出口②）。
4. **软压缩**：重新估算 token 量，到了 SOFT 或 HARD 就在**回合边界**压缩——此刻当前推理已结束，压缩不打断它。这就是"同一个压缩动作、两个时机"的另一半（HARD 在调用前、SOFT 在回合末）；具体压什么、怎么压，第 07 篇拆。

> ### 【Python 加餐 19】多值返回与解包（Java 对照）
>
> `should_stop, reason = self._policy.check(state)`——一行从一次函数调用里接出两个值。Java 没有这个能力：要么返回 `boolean[]`（丑），要么定义 `StopDecision` 类（重），要么传两个出参容器（更丑）。Python 的方案是**元组解包**：
>
> ```python
> def check(self, state) -> tuple[bool, str]:
>     ...
>     return False, ""          # 返回的其实是 ("False 的元组"，"" ) 一个元组对象
>
> should_stop, reason = check(state)   # 就地拆成两个名字
> ```
>
> 逗号即元组（不需要写括号）；赋值左侧放几个名字，右侧的元组就按位置拆几份——**"接收多个返回值"其实是"接收一个元组再解包"**。数量对不上会抛 `ValueError: too many/few values to unpack`（运行时检查——又一次"Python 的检查在运行期"）。
>
> 这个语法在项目里到处都是，模式识别一下：
>
> ```python
> for idx, f in enumerate(shown, start=1):     # 遍历时同时拿序号和元素
> for rel, source in sources:                  # 遍历二元组列表
> for dirpath, dirnames, filenames in os.walk(root):   # 库函数的"三元组"
> ok, reason = file_filter.should_include(path)        # 判断 + 原因
> located, project = _locate_finding(store, finding_id)
> ```
>
> Java 的 `for (int i = 0; i < list.size(); i++)` 在 Python 里几乎绝迹——`enumerate` 直接给序号（**`i, x` 谁在前？记住"序号在前"**，`enumerate(x)` 产出 `(index, item)`）。还有两个配套工具先认识：`zip(a, b)` 把两个列表配对成元组流（`for name, score in zip(names, scores)`）；`a, b = b, a` 交换两值（不需要临时变量——右侧先构造元组，再解包回写）。**项目里的多值返回全部走元组，没有一处"输出参数"**——这是 Python 风格和 Java 风格在 API 设计上的一个显著差异。

### 收尾：把账本交出去

```python
        # ---- 收尾
        result.turns = state.turn
        result.tool_calls_total = state.tool_calls_total
        result.usage = self._budget.usage
        result.compress_events = self._compress_count
        self._emit("end", {"status": result.status, "reason": result.stop_reason})
        return result
```

四个账目从各器官汇集到 `AgentResult`：

| 字段 | 来源 | 最终去向 |
|---|---|---|
| `turns` | `state.turn` | 报告"轮次 N" |
| `tool_calls_total` | `state.tool_calls_total` | 调试/讲述用 |
| `usage` | `budget.usage`（含压缩调用） | 报告的 Token 与成本 |
| `compress_events` | `self._compress_count` | 报告"压缩 N 次" |
| `final_message` | 3a 分支写入 | （目前未在报告展示） |

回到 `pipeline.py`，这四个值被接走：`loop_usage = result.usage`、`turns`、`stop_reason`、`compress_events`——最终填进 `ReviewReport`。

## 5.3 依赖注入：循环为什么"什么都不认识"

`AgentLoop` 的构造函数是理解这个内核设计哲学的钥匙：

```python
    def __init__(
        self,
        *,
        client: Any,  # LLMClient 或兼容对象（Mock 亦可）
        registry: ToolRegistry,
        memory: WorkingMemory,
        budget: TokenBudget,
        estimator: TokenEstimator,
        model: str,
        policy: StopPolicy | None = None,
        config: LoopConfig | None = None,
        compressor: Compressor | None = None,
        findings_counter: Callable[[], int] | None = None,
        on_event: EventHook | None = None,
    ) -> None:
```

**全部仅关键字参数（`*` 打头）**——十一个参数的构造调用，位置传参是灾难，`*` 从语法上强制每个参数带名字（加餐 15 提过 `make_finding` 的 `*`，同一个手法）。

两个参数的类型别名值得单独看：

```python
# 压缩器：接收"中间区消息列表"，返回结构化摘要文本（由外部注入 LLM 实现）
Compressor = Callable[[list[dict[str, Any]]], str]
# 事件钩子：(事件名, 载荷)
EventHook = Callable[[str, dict[str, Any]], None]
```

`Compressor` 的实现（调 LLM 做摘要）在第 07 篇、组装在 pipeline——**循环只负责在"合适的时机"喊一声"该压缩了"，完全不知道压缩内部是 LLM 调用**。`findings_counter` 同理：循环只知道"每轮末问一把发现数"。这就是文件头那句"循环不认识压缩细节与发现收集"的字面含义——**关注点分离不是口号，是构造函数的参数表**。

这不只是美学。三个直接收益：

1. **可测试**：`tests/test_agent_loop.py` 用 `MockLLMClient` + 假压缩器把九个场景全测了——循环的所有依赖都是参数，替换任何一个不需要打桩框架（第 11 篇的测试地图会回来）；
2. **可替换**：把 `client` 换成别的 LLM 兼容对象、把 `compressor` 换成规则版摘要——循环零改动；
3. **可观测**：所有决策点都往 `on_event` 发事件，CLI 进度、调试、评测 trace 消费同一个事件流。

事件钩子还有一道保险：

```python
    def _emit(self, event: str, payload: dict[str, Any]) -> None:
        if self._on_event is not None:
            try:
                self._on_event(event, payload)
            except Exception:  # noqa: BLE001 - 事件钩子异常不影响主循环
                pass
```

**观测设施不能反噬主流程**——传入的进度打印函数就算抛异常，审查照跑。

一次完整审查的事件流（把 CLI 实际能看到的按顺序拼起来）：

```text
start        {tools: [7 个名字]}
context      {turn: 0, tokens: 4821, ratio: 0.075, state: ok, messages: 2}
tool_calls   {turn: 1, names: [read_file, run_analyzer]}
tool_result  {name: read_file, preview: "# order_processor.py（共 31 行…"}
tool_result  {name: run_analyzer, preview: "bandit 在 order_processor.py 发现…"}
context      {turn: 1, tokens: 9635, ratio: 0.15, state: ok, messages: 6}
tool_calls   {turn: 2, names: [submit_finding]}
tool_result  {name: submit_finding, preview: "已记录发现 #1：可变默认参数：process_orders…"}
...
compress     {removed: 6, kept: 8, context_tokens: 39400}
context      {turn: 5, tokens: 38211, ratio: 0.597, state: soft, messages: 11}
finish       {message: "本次审查共复核 N 条候选…"}
end          {status: finished, reason: 模型主动结束}
```

把这串事件和 5.2 的代码对照着读，每一行都能找到出处——**这就是"每一步可观测"的含义**。

> ### 【Python 加餐 20】作用域规则：LEGB 与闭包里的 `nonlocal` ★
>
> 一个变量名被用到时，Python 按固定顺序找它的定义——**LEGB**：
>
> ```text
> L (Local)      当前函数的局部作用域
> E (Enclosing)  外层函数的作用域（闭包！）
> G (Global)     模块级（.py 文件顶层）
> B (Builtin)    内置名字（len、print、open…）
> ```
>
> 本项目里 LEGB 的三个实例，随口就能举出来：
>
> 1. **L → G**：`loop.py` 里的 `while True` 用到 `BudgetState.ABORT`——找不到局部定义就往上走，最终在模块顶部的 `from codesage.llm.budget import BudgetState` 里找到（G 层）；
> 2. **E（闭包的铁证）**：`read_file` 工具函数体里用 `root` 和 `limit`——它们既不在 L 也不在 G，是外层 `make_read_file_tool` 的参数（第 04 篇加餐 16）。`_make_compressor` 返回的 `compressor` 用 `client`/`router`/`budget` 同理；
> 3. **B**：到处在用的 `len()`、`isinstance()`、`min()` 都来自 Builtin 层——**Python 没有 import 就能用的那批内置函数**，全在这个层。
>
> **和 Java 的关键差异**：Java 是块级作用域（`if` / `for` 的花括号就是边界），Python 是**函数级作用域**（只有 def/class/lambda 和模块划界）：
>
> ```python
> for i in range(10):
>     ...
> print(i)   # ✅ i 还活着，值是 9——Java 程序员的惊悚时刻
> ```
>
> **修改外层变量需要声明**。闭包里"读"外层变量天经地义，"写"要显式声明归属：
>
> ```python
> def counter():
>     n = 0
>     def tick():
>         nonlocal n      # 声明：n 是外层的，别在本地新建
>         n += 1          # 没有 nonlocal 这一行会直接 UnboundLocalError
>     return tick
> ```
>
> 模块级变量则用 `global`。项目里有没有用到？一个精妙的例子在循环自己身上：`self._compress_count += 1`（5.3 节马上看到）——**`self` 是个普通参数**，`self._compress_count` 是"对 self 这个对象的属性赋值"，不涉及作用域声明；而 `_compress` 方法里读 `self._compressor` 也是属性访问。**OOP 语法天然绕开了作用域问题**，这就是为什么项目里几乎不出现 `global` / `nonlocal`。
>
> 最后一条实战知识：**局部变量访问最快**（L 层是数组索引，G/B 层要查字典）——性能敏感的内层循环里，把 `len` 之类的全局名"搬"进局部是经典微优化；本项目规模不需要，但你在读 CPython 源码时会大量见到。

`_compress()` 的内部——循环对压缩的"全部知识"：

```python
    def _compress(self, context_tokens: int | None = None) -> None:
        """执行一次压缩（无压缩器注入时为空操作）。"""
        if self._compressor is None:
            return
        event = self._memory.compress(self._compressor)
        if event is not None:
            self._compress_count += 1
            self._emit(
                "compress",
                {
                    "removed": event.removed_messages,
                    "kept": event.kept_messages,
                    "context_tokens": context_tokens,
                },
            )
```

三层含义逐层看：没注入压缩器 → 空操作（循环照转）；`memory.compress(...)` 返回 None → 无需压缩或压缩失败（第 07 篇：宁可占 token 不可丢信息）；成功 → 计数 + 发 `compress` 事件（CLI 黄字"记忆压缩触发（移除 N 条消息，保留 M 条）"）。

## 5.4 停止策略：三种死法，其中一种有宽限期

`agent/policies.py` 的 `StopPolicy` 决定循环何时被"掐停"。四类停止条件的清单（文件头）：

```python
"""停止条件策略：Agent 循环的安全阀。

四类停止条件（对应规划文档）：
1. 模型主动结束（无 tool_calls）—— 正常完成；
2. 轮次上限 —— 防止无限循环；
3. Token 预算熔断（在 loop 中检查）；
4. 连续 N 轮无新增发现 —— 防止"原地打转"式低效循环。
"""
```

注意第 3 条的括号："在 loop 中检查"——**策略只管它能看到的（轮次、进展），预算检查留在循环主流程**。职责切得很细。

完整实现（这一版比初版更聪明，注释里的"教训来自真实案例"值得读）：

```python
    def check(self, state: AgentState) -> tuple[bool, str]:
        """返回 (是否停止, 原因)。

        无进展判定的两种情形（教训来自真实案例——LLM 在前 3 轮"调查期"
        读码/跑分析/查知识库被过早掐断）：
        - 已提交过发现后连续 N 轮无新增 → 停滞，停止；
        - 从未提交过任何发现 → 给更宽的宽限期（调查本身是进展，
          模型可能只是尚未到提交时机），宽限用尽才判定完成。
        """
        if state.turn >= self.max_turns:
            return True, f"达到轮次上限（{self.max_turns} 轮）"

        history = state.findings_history
        if not history:
            return False, ""

        ever_found = any(count > 0 for count in history)
        patience = (
            self.no_progress_patience
            if ever_found
            else self.no_progress_patience + 2  # 零提交宽限期
        )
        if state.turn >= 3 and len(history) > patience:
            recent = history[-(patience + 1):]
            if len(set(recent)) == 1:
                if ever_found:
                    return True, f"连续 {patience} 轮无新增发现（当前共 {recent[-1]} 条）"
                return True, f"连续 {patience} 轮未提交任何发现（判定审查完成）"
        return False, ""
```

### 判定一：轮次上限

```python
        if state.turn >= self.max_turns:
            return True, f"达到轮次上限（{self.max_turns} 轮）"
```

最直白的兜底。`max_turns` 默认 12（来自 `StopPolicy(max_turns=options.max_turns or settings.review.max_turns)`）。

### 判定二：无进展——两种情形分开对待（本轮的智慧）

先看"无进展"的原料：`findings_history` 是每轮末记录的累计发现数，比如 `[0, 0, 2, 5, 5, 5]`。

```python
        if not history:
            return False, ""

        ever_found = any(count > 0 for count in history)
```

`ever_found = any(count > 0 for count in history)`——**生成器表达式**（加餐 11）+ `any`：历史上有没有拿到过发现。

然后**耐心值分档**：

```python
        patience = (
            self.no_progress_patience
            if ever_found
            else self.no_progress_patience + 2  # 零提交宽限期
        )
```

- **交过作业的**（`ever_found=True`）：耐心 = 2，连续 2 轮无新增就停；
- **一个发现都没交过的**：耐心 = 2 + 2 = 4——**宽限期**。

为什么要分档？注释里的"教训"讲得清楚：LLM 在前几轮是**调查期**——读代码、跑分析器、查知识库，这些轮次 `findings_history` 里是清一色 0。旧版本用统一耐心，会在调查中途把它掐断："你还没发现任何东西，停吧"——然后报告是空的。新版本认识到：**"还没找到 + 正在找" ≠ "找不到"**——多给两轮，只有连调查都看起来没有产出时才判定"审查完成"。

判定条件本身：

```python
        if state.turn >= 3 and len(history) > patience:
            recent = history[-(patience + 1):]
            if len(set(recent)) == 1:
```

- `state.turn >= 3`：至少跑满 3 轮才有资格谈"连续 N 轮"；
- `history[-(patience + 1):]`：**负索引切片**——取最近 `patience + 1` 个值（第 07 篇加餐 23 细讲切片；这里读作"从倒数第 patience+1 个到末尾"）；
- `len(set(recent)) == 1`：**集合去重后只有一个元素** = 这些值全部相等 = 最近这么多轮一个新增都没有。用 `set` 判"全部相同"是 Python 的经典小技巧（比逐对比较干净）。

最后按情形返回不同的停止语：

```python
                if ever_found:
                    return True, f"连续 {patience} 轮无新增发现（当前共 {recent[-1]} 条）"
                return True, f"连续 {patience} 轮未提交任何发现（判定审查完成）"
```

两种文案都在报告里出现（`stop_reason` → 终端"结束：…"）。**让用户看懂"为什么停"**——"连续 4 轮未提交任何发现（判定审查完成）"和"连续 2 轮无新增发现（当前共 7 条）"是两种完全不同的故事。

测试对两条路径都有锁定（第 11 篇展开）：`test_max_turns_stops` / `test_no_progress_stops` 用 `findings_counter=lambda: 0` 构造出必停场景。

## 5.5 错误回填：三层防线，一个哲学

整个循环"看起来没有多少错误处理"——不是因为没做，而是因为**错误在到达循环之前已经被消化了三层**。完整链条：

```text
第一层 · 客户端（llm/client.py）
  任何 SDK 异常 → 统一包装 raise LLMError
  tool_calls 参数 JSON 解析失败 → 不抛，记在 ToolCall.parse_error（第 07 篇）

第二层 · 工具注册表（agent/registry.py）
  未知工具 / 参数不匹配 / 工具内部异常 → 全部转成 "ERROR: ..." 文本返回
  （第 06 篇细讲）

第三层 · 循环（agent/loop.py）
  只处理 LLMError（出口④）；
  工具永远不抛异常 → 循环的工具执行段没有 try；
  错误文本作为 role=tool 消息回填 → 模型下一轮自己纠正
```

**设计原则一句话：错误是"可回填的信息"，不是"该终止的事件"。** 除非 LLM 本身调用失败（没有信息可回填，只能退出），其余错误都变成文本注入对话，让模型看到并调整策略——"工具名写错了 → 看到可用工具清单 → 改对"、"参数缺了 → 看到 required 列表 → 补上"、"文件不存在 → 换个路径再读"。

这套设计有测试投影（`tests/test_agent_loop.py` 的三个场景）：未知工具、参数缺失、工具内部抛异常——每个都断言循环最终 `status == "finished"`（**没崩**）且下一轮请求里带着 `ERROR` 开头的 tool 消息。

## 5.6 小结与下一站

- **一轮五步**：预算检查（ABORT 熔断 / HARD 强压）→ LLM 调用 → 分支（无工具=完成；有工具=执行回填）→ 回合末（记进展、停止检查、SOFT 软压）。
- **四个出口**：`finished` / `stopped` / `aborted` / `error`——全部带文案进报告。
- **依赖注入**：十一个仅关键字参数；`Compressor`/`findings_counter`/`on_event` 都是"外部函数"——循环不认识任何器官的内部；可测试、可替换、可观测。
- **停止策略**：轮次上限 + 无进展检测（**已交过发现=耐心 2 轮；零提交=宽限 4 轮**——来自真实案例的改进）；预算检查留在循环里。
- **错误哲学**：三层防线 + "错误是可回填信息"；工具执行段不需要 try 的原因在这里。
- **Python 加餐**：真值与 None（★）、多值返回与解包、LEGB 作用域（★）。

下一站是循环的"手"——**七个工具逐个看**。循环在 3b 里调用 `registry.execute(name, arguments)` 时，那七个工具到底各是什么签名、返回什么格式、失败时说什么话、以及 `execute()` 的三道防线怎么运转，全部展开。

# 《CodeSage项目》——第 7 站 · 阶段三·后勤：记忆、预算与 LLM 通信

> 📍 **主线进度**：起点 ✅ → 扫描 ✅ → 确定性分析 ✅ → 组装 ✅ → 循环 ✅ → 工具 ✅ → **▶ 后勤（本篇）** → 合并与输出

循环转起来了，工具也在响了。但每一次 LLM 调用、每一条工具结果、每一次压缩，都在吃同一种稀缺资源：**上下文窗口**。这一篇讲循环背后的后勤系统——三区记忆怎么管理上下文（7.1–7.2）、预算怎么防止失控（7.3）、估算器怎么量它（7.4）、路由器怎么分配模型（7.5）、客户端怎么和 LLM 说话（7.6）。这是"上下文经济学"的全部账本。

## 7.1 三区记忆：冻结、摘要、活跃

`memory/working.py` 的模块头把模型画了出来：

```text
三区模型：
    ┌────────────────────────────────────────────┐
    │ 冻结区 frozen：系统提示 + 任务简报            │  永不压缩
    │ （跨轮次完全不变 → 可命中 prompt 缓存）        │
    ├────────────────────────────────────────────┤
    │ 压缩区 summary：中间轮次的结构化摘要           │  由 LLM 压缩产生
    │ （保留：已提交发现 / 审查进度 / 未决疑点）      │
    ├────────────────────────────────────────────┤
    │ 活跃区 active：最近 K 轮原文                  │  保持原样
    │ （保证推理连续性）                            │
    └────────────────────────────────────────────┘
```

对应到数据结构：

```python
@dataclass
class WorkingMemory:
    """三区工作记忆。"""

    active_window_turns: int = 4
    _frozen: list[dict[str, Any]] = field(default_factory=list, repr=False)
    _active: list[dict[str, Any]] = field(default_factory=list, repr=False)
    _summary: str = ""
    _events: list[CompressEvent] = field(default_factory=list, repr=False)
```

| 字段 | 内容 | 生命周期 |
|---|---|---|
| `_frozen` | 系统提示 + 任务简报（2 条消息） | 进循环时注入，永不改动 |
| `_summary` | 压缩区（**一个字符串**，不是消息列表） | 每次压缩后重写 |
| `_active` | 最近 K 轮的完整原文消息 | 追加；压缩时切掉前半段 |
| `_events` | 压缩事件记录（CompressEvent 列表） | 追加（当前无外部读取方） |

三个区的职责各回答一个问题：

- **冻结区——什么永远不变？** 系统提示词 + 任务简报。进循环第一件事就是 `self._memory.freeze(initial_messages)`（第 05 篇），此后不再改动。**永不改动才有 prompt 缓存的机会**：主流 LLM 的缓存按"请求前缀"匹配，前缀稳定是命中前提——冻结区每次请求都以原样出现，是缓存命中率的最大保障。
- **活跃区——什么必须保真？** 最近 K 轮（默认 `active_window_turns=4`，配置可调）的完整消息：assistant 的工具调用、tool 的完整结果。模型对最近发生的事情保持"精确记忆"，推理连续性靠它。
- **压缩区——什么可以丢细节、不能丢信息？** 更早的轮次被 LLM 压缩成结构化摘要——注意它是**一个字符串**而不是消息列表：压缩产物的形态由压缩 prompt 定义（见 7.2），最终以一条 `role=system` 的消息注入。**摘要进系统角色而不是用户角色**——放在"系统告诉你的事实"而不是"用户说的话"的位置。

组装顺序在 `build()` 里是固定的：

```python
    def build(self) -> list[dict[str, Any]]:
        """组装完整消息序列：冻结区 + 摘要 + 活跃区。"""
        out: list[dict[str, Any]] = list(self._frozen)
        if self._summary:
            out.append(
                {
                    "role": "system",
                    "content": (
                        "## 历史审查过程摘要（此前轮次已压缩，发现均已记录）\n"
                        f"{self._summary}"
                    ),
                }
            )
        out.extend(self._active)
        return out
```

注意摘要消息的模板文案："此前轮次已压缩，**发现均已记录**"——这是写给模型的心理暗示：不要因为看不到早期原文而慌，需要的信息都在摘要和你的提交记录里。这个 `build()` 就是循环每次 `client.chat(self._memory.build(), ...)` 喂给 LLM 的东西——**消息序列的完整面貌在第 7.6 节末尾给出**。

另外注意 `field(repr=False)`——dataclass 的 `repr` 开关：打印 `WorkingMemory` 时隐藏这些内容字段（消息内容太长，调试时看个摘要就好）。

> ### 【Python 加餐 23】切片与序列操作（Java 对照）
>
> 切片（slicing）是 Python 序列（字符串/列表/元组）的招牌能力，`[start:stop:step]` 三段式，**左闭右开**：
>
> ```python
> s = "abcdef"
> s[1:4]     # "bcd"（下标 1、2、3）
> s[:3]      # "abc"（省略 start = 从头开始）
> s[3:]      # "def"（省略 stop = 到结尾）
> s[-3:]     # "def"（负数 = 从尾部数）
> s[::2]     # "ace"（步长 2）
> s[::-1]    # "fedcba"（步长 -1 = 反转——Python 没有 reverse() 字符串方法，这就是惯用法）
> ```
>
> 项目里的切片用法随手可摘：
>
> ```python
> history[-(patience + 1):]     # 最近 patience+1 个元素（第 05 篇停止策略）
> output[:120]                   # 前 120 个字符（事件预览）
> text[:600] / text[:900]        # 截断单条消息（压缩序列化，本篇 7.2）
> suffix = line[4:].strip()      # 去掉 "+++ " 前缀（git diff 解析，第 02 篇）
> sorted_candidates[:max_candidates]   # 截断列表（任务简报，第 04 篇）
> ```
>
> **和 Java 的对照**：Java 的 `substring(start, end)` / `subList(start, end)` 只支持正索引、要新建对象、左闭右开但必须在长度内；Python 切片支持负索引、越界自动夹紧（`s[:9999]` 不报错，给你全串——**写"最多前 N 个"时不用先 min**）、step 任意方向。一行 `s[::-1]` 反转在 Java 里得 `new StringBuilder(s).reverse().toString()`。
>
> 两个坑记一下：**切片总是产生新序列**（新的 list/str、新的引用），不改原对象——要原地改列表用 `del lst[i:j]` 或 `lst[i:j] = [...]`；对**列表**切片是浅拷贝（元素还是共享引用，加餐 13 的老问题）。
>
> 另一个日常操作是 `+` 与 `extend` 的分工：`list_a + list_b` 生成**新列表**（费用 = 两边全部复制）；`list_a.extend(list_b)` **原地追加**（像 Java 的 `addAll`）。性能敏感路径上这个区别很实在——第 08 篇合并发现时用的就是 `extend`。

## 7.2 压缩机制全解：从触发到产物

### 触发：回顾两个时机

第 05 篇见过两处调用：**调用前的 HARD 强制压缩**、**回合末的 SOFT/HARD 软压缩**。触发方（loop）与执行方（memory）的分离再强调一遍——`WorkingMemory` 自己**不决定**什么时候压缩，只提供 `compress()` 动作。

### 执行：`compress()` 逐行

```python
    def compress(self, compressor: Compressor) -> CompressEvent | None:
        """压缩中间区（活跃区中除最近 K 轮外的部分）。

        返回 None 表示：无需压缩（轮数不足）或压缩失败（保持原状）。
        """
        keep_from = self._find_keep_index()
        if keep_from <= 0:
            return None  # 活跃区不足 K 轮，不压缩

        middle = self._active[:keep_from]
        payload = self._serialize(middle)
        if not payload.strip():
            return None

        prompt = COMPRESS_PROMPT.format(
            previous=(
                f"## 已有摘要（与本次新内容合并，不可丢失其中信息）\n{self._summary}\n\n"
                if self._summary
                else ""
            ),
            payload=payload,
        )

        try:
            new_summary = compressor(prompt)
        except Exception:  # noqa: BLE001 - 压缩失败保持原状（信息优先于成本）
            return None

        if not new_summary or not new_summary.strip():
            return None

        event = CompressEvent(
            removed_messages=len(middle),
            kept_messages=len(self._active) - keep_from,
            summary_chars=len(new_summary),
        )
        self._summary = new_summary.strip()
        self._active = self._active[keep_from:]
        self._events.append(event)
        return event
```

步骤链：**找切割点 → 切出中间区 → 序列化 → 套压缩 prompt → 调压缩器 → 失败保护 → 更新状态、记录事件**。

### 切割点：`_find_keep_index` 的协议细节

```python
    def _find_keep_index(self) -> int:
        """找到活跃区应保留的起始下标（保留最近 K 轮完整）。

        轮边界定义：assistant 消息开始一轮，其后跟着该轮的 tool 结果。
        cut 点必须落在某个 assistant 处——保证不切断 assistant-tool 配对
        （配对断了会造成 OpenAI 协议错误：tool 消息找不到对应的 tool_call）。
        返回 0 表示当前轮数不足、无需压缩。
        """
        assistant_indices = [
            idx
            for idx, msg in enumerate(self._active)
            if msg.get("role") == "assistant"
        ]
        if len(assistant_indices) <= self.active_window_turns:
            return 0
        return assistant_indices[-self.active_window_turns]
```

这是整个压缩机制里**技术含量最高的一段**，每个约束都有来由：

- **"轮"的定义**：一条 assistant 消息 = 一轮的开始，后面跟着它的 tool 结果。列表推导式（加餐 11）收集全部 assistant 消息的下标；
- ****必须**从 assistant 处切**：function calling 协议要求每条 `role=tool` 消息都能对应到之前某条 assistant 消息里的 `tool_call_id`。如果切割点落在"assistant 之后、它的 tool 结果之前"，被切走的 tool 消息就成为孤儿——下一次 API 调用**直接协议报错**。所以切点只能选「倒数第 K 个 assistant 的下标」——**配对作为整体保留或整体进压缩区**；
- `assistant_indices[-self.active_window_turns]`：负索引取"倒数第 K 个"（加餐 23 的知识）；
- **不足 K 轮直接返回 0**：活跃区还没攒够就没什么可压的。

这种协议层的坑，`test_memory.py` 有专门的用例覆盖（"压缩后 assistant-tool 配对不破坏"）。

### 序列化：给压缩器喂什么

`_serialize()` 把中间区消息转成纯文本，并做**防二次膨胀的截断**：

```python
    @staticmethod
    def _serialize(messages: list[dict[str, Any]]) -> str:
        """把中间区消息序列化为压缩器输入（控制单条长度，防止二次膨胀）。"""
        parts: list[str] = []
        for msg in messages:
            role = msg.get("role", "?")
            content = msg.get("content") or ""
            if role == "assistant":
                tool_calls = msg.get("tool_calls") or []
                names = [
                    (tc.get("function") or {}).get("name", "?")
                    for tc in tool_calls
                    if isinstance(tc, dict)
                ]
                head = f"[assistant] 调用工具：{', '.join(names)}" if names else "[assistant]"
                parts.append(f"{head}\n{str(content)[:600]}")
            elif role == "tool":
                parts.append(f"[tool 结果] {str(content)[:900]}")
            else:
                parts.append(f"[{role}] {str(content)[:600]}")
        return "\n---\n".join(parts)
```

三条纪律：单条 assistant/user 内容截 600 字符、tool 结果截 900 字符（**"防止二次膨胀"——不然压缩输入本身就撑满上下文，压缩还没做就超了**）；assistant 消息把"调用了哪些工具"提取成头行（这是重要的过程信息）；消息之间用 `---` 分隔。嵌套取值链 `(tc.get("function") or {}).get("name", "?")` 是处理"不可信 JSON 结构"的惯用防御——任何一层缺失都拿到 `"?"` 而不是崩溃。

### 压缩提示词：防丢失约束

```text
你是代码审查过程的信息压缩器。把下面的审查过程压缩为结构化摘要。

压缩要求（严格执行）：
1. 必须完整保留：所有已提交的发现（编号 + 文件:行号 + 一句话结论）、
   已审查的文件清单、未决疑点与待办。
2. 丢弃：重复的文件内容、冗长的工具原始输出、已完成的分析细节。
3. 输出为简洁的要点列表（纯文本），不要任何寒暄或说明。
4. 严禁遗漏“已提交的发现”——它们是最终报告的核心数据。

{previous}## 审查过程原文

{payload}
```

四条要求对应四个设计决策：

1. **保留清单是白名单式**：发现/进度/疑点三类必须完整保留——对应规划里的"结构化压缩而非自然语言摘要"原则（产物是清单，可检索、无歧义）；
2. **丢弃也立规矩**：明确列出可以丢的东西，防止模型为省事丢错；
3. **禁寒暄**：压缩产物直接进上下文，不能有"好的，以下是摘要"这样的噪音；
4. **第二次压缩的合并语义**：`{previous}` 槽在有旧摘要时填入"## 已有摘要（与本次新内容合并，不可丢失其中信息）"——**压缩是滚雪球式的**：新摘要必须吞下旧摘要。`test_memory.py` 有一条测试专门验证"旧摘要参与了二次压缩"。

### 失败保护：宁可占 token，不可丢信息

两道保护（代码已在上面出齐）：压缩器**抛异常** → `except` 返回 None；**返回空串** → 同样返回 None。两种情况都**不做任何状态改动**——`self._summary` 和 `self._active` 原样。测试锁定的原则（`test_memory.py` 注释原文）：**「信息优先于成本」**——压缩失败最坏的结果是多占点 token，绝不能出现"摘要生成失败但原文已经丢了"。

### 压缩器本体：一次快模型调用

```python
def _make_compressor(client: LLMClient, router: ModelRouter, budget: TokenBudget):
    """构造记忆压缩器（用快模型，成本优先）。

    注入给 WorkingMemory：输入压缩 prompt，输出结构化摘要。
    """

    def compressor(prompt: str) -> str:
        response = client.chat(
            [{"role": "user", "content": prompt}],
            model=router.model_for(TaskKind.COMPRESS),
            temperature=0.0,
        )
        budget.record(response.usage)
        return response.content

    return compressor
```

三个细节：**快模型**（`TaskKind.COMPRESS` 路由到 `model_fast`——摘要任务不需要强模型）；**temperature=0.0**（摘要要稳定，不要创意）；**`budget.record(response.usage)`**——压缩消耗的 token 计入**同一本账**，报告里的总 token 和成本不会漏掉压缩开销。这也是"压缩本身也要花钱"的诚实体现——第 7.3 节算总账。

## 7.3 Token 预算：四个状态、三道闸、一笔总账

`llm/budget.py` 的模块头：

```python
"""Token 预算与熔断。

三层阈值（与三区记忆模型配套）：
    ratio < soft   → 正常执行
    ratio ≥ soft   → SOFT：回合边界触发压缩（不打断当前推理）
    ratio ≥ hard   → HARD：调用前强制压缩（阻塞，保证下一轮能放下）
    ratio ≥ abort  → ABORT：熔断，结束循环并输出已有发现（止损）

为什么需要熔断：宁可输出“不完整但不报错”的结果，也不让 API 调用
因超限失败导致整个审查白跑。
"""
```

状态判定是自上而下的水位检查：

```python
    def state(self, current_context_tokens: int) -> BudgetState:
        ratio = self.ratio(current_context_tokens)
        if ratio >= self._settings.abort_ratio:
            return BudgetState.ABORT
        if ratio >= self._settings.hard_ratio:
            return BudgetState.HARD
        if ratio >= self._settings.soft_ratio:
            return BudgetState.SOFT
        return BudgetState.OK
```

默认参数换算成具体数字（`context_window=64000`）：

| 状态 | 阈值 | 触发点 | 行为 |
|---|---|---|---|
| OK | < 60% | < 38400 tokens | 正常执行 |
| SOFT | ≥ 60% | 38400 | 回合边界压缩（不打断推理） |
| HARD | ≥ 80% | 51200 | 调用前强制压缩（阻塞） |
| ABORT | ≥ 95% | 60800 | 熔断：结束循环，输出已有发现 |

**为什么阈值之间留大间距**：压缩本身要消耗"时间"（一次 LLM 调用的往返）和"空间"（压缩 prompt 自身几百 token）——在 60% 就开始处理，等于给自己留了 20% 的操作空间。这也回答了估算器为什么敢用启发式：**±20% 的估算误差，正好被阈值间的安全垫吸收**。

**熔断的账**：没有 ABORT 会怎样——上下文逼近上限 → 某次调用被服务端拒绝（超限错误）→ `LLMError` → 循环以 `error` 退出。结果：前面几轮的发现能保住，但**用户看到的是一个错误而不是报告**。有熔断：最后一轮检查发现 ≥95%，主动退出、把已有发现全部带走、报告正常生成（结束原因里写明"Token 预算熔断（使用率 ≥ 95%），输出已有发现"）。**"不完整但可用"优于"完整但崩溃"**——长流程 Agent 的通用生存法则。

预算对象还顺手记账：

```python
    def record(self, usage: TokenUsage) -> None:
        """记录一次真实调用用量（以 API 返回为准）。"""
        self.usage.add(usage)

    def cost_yuan(self) -> float:
        """按配置单价估算累计成本（元）。"""
        return self.usage.cost_yuan(
            self._settings.input_price_per_m,
            self._settings.output_price_per_m,
        )
```

`record` 的数据来自每次 API 响应里的真实 usage（不是估算）——循环主调用、压缩调用都记；`cost_yuan` 按配置单价（默认输入 2 元/输出 8 元每百万 token）出成本。报告里的"¥0.0X"就是它的产物。**注意 `usage.add` 是自定义的加法方法**（`TokenUsage` 是 dataclass 不是数字类型）——把一次调用的用量并入累计账本。

## 7.4 Token 估算器：为什么自己写一个

预算系统的"传感器"是估算器。行业标准答案 tiktoken，项目却自研了一个启发式——`llm/tokenizer.py` 头注释给出完整权衡：

```python
"""Token 估算器：预算控制的基础设施。

设计取舍（为什么默认不用 tiktoken）：
- tiktoken 首次运行需要联网下载 BPE 编码文件，国内环境容易卡住；
- 预算控制只需要 ±20% 量级的精度（熔断阈值留有安全余量），
  启发式估算完全够用；
- 抽象出 TokenEstimator 接口，需要高精度时一行切换（prefer_tiktoken=True）。
"""
```

公式简单到可以背下来——**CJK 字符 1 字符 ≈ 1 token；其余字符 3.5 字符 ≈ 1 token**：

```python
class HeuristicEstimator:
    """启发式估算器（零依赖）。

    规则（对 GPT/DeepSeek 系 BPE 分词的经验近似）：
    - CJK 字符：约 1 字符 = 1 token
    - 其余字符（英文/代码/符号）：约 3.5 字符 = 1 token

    实际误差通常在 ±20% 以内，用于“何时触发压缩/熔断”的决策足够。
    """

    CJK_RANGE = ("\u4e00", "\u9fff")

    def estimate(self, text: str) -> int:
        if not text:
            return 0
        cjk = 0
        for ch in text:
            if self.CJK_RANGE[0] <= ch <= self.CJK_RANGE[1]:
                cjk += 1
        other = len(text) - cjk
        return int(cjk + other / 3.5) + 1
```

逐字统计汉字（`\u4e00`~`\u9fff` 区间）、其余按 3.5:1 折算、`+1` 基准。消息级估算再补结构开销：

```python
    def estimate_messages(self, messages: list[dict[str, Any]]) -> int:
        """估算完整消息列表（含角色开销与 tool_calls 的 JSON 体积）。

        每条消息额外 +4 token（OpenAI 消息包装开销的经验值），
        整个请求再 +3。
        """
```

`tool_calls` 字段的 JSON 体积也被单独估算（序列化后估，失败兜底 +50）——**带工具调用的 assistant 消息比纯文本大得多**，不估这块会明显低估。

升级路径是"一行切换 + 静默回退"：

```python
def get_estimator(prefer_tiktoken: bool = False) -> TokenEstimator:
    """获取估算器：默认启发式，可尝试升级为 tiktoken。"""
    if prefer_tiktoken:
        try:
            return TiktokenEstimator()
        except Exception:  # noqa: BLE001 - 任何失败都优雅回退
            pass
    return HeuristicEstimator()
```

就算显式要 tiktoken，导入失败（`TiktokenEstimator.__init__` 里的 `import tiktoken` 抛 ImportError）也静默回退——**依赖的高可用性优先于精度**。估算器的行为被 `test_tokenizer.py` 用区间断言锁定（"4 个 CJK 字符 ≈ 4~6 token"、"35 个 ASCII ≈ 9~12 token"）——**测试不和公式的精确值较劲，只锁定量级**，这正是"±20% 够用"的工程态度。

> ### 【Python 加餐 24】Protocol 与结构化类型（Java 对照）
>
> 估算器有一个"接口"定义：
>
> ```python
> @runtime_checkable
> class TokenEstimator(Protocol):
>     """估算接口。"""
>
>     def estimate(self, text: str) -> int:
>         """估算一段文本的 token 数。"""
>         ...
> ```
>
> **`Protocol` 是 Python 表达"接口"的方式，但和 Java interface 的哲学相反**：Java 要求实现类显式声明 `implements TokenEstimator`，是**名义类型**（nominal typing）；Protocol 是**结构化类型**（structural typing）——任何类只要**碰巧**有符合签名的 `estimate` 方法，就自动"满足"这个接口，**不需要继承、不需要注册**：
>
> ```python
> class HeuristicEstimator:      # 注意：没有写任何继承
>     def estimate(self, text: str) -> int: ...
>
> est: TokenEstimator = HeuristicEstimator()   # 类型检查器会放行
> ```
>
> 这就是"鸭子类型"的静态版本——**行为对了就是对了**。"碰巧满足"的可替换性正是加餐 17 讲过的鸭子类型，只不过 Protocol 让静态检查器（mypy/Pyright）也能提前发现"原型不匹配"，补上了编译期的安全感。
>
> 两个实用装饰/细节：`@runtime_checkable` 让 `isinstance(obj, TokenEstimator)` 能在运行时做检查（没人先查形状再赋值时可省略）；`...`（Ellipsis）作为方法体是协议类的惯例——**这里只需要签名，不需要实现**。
>
> 记忆对照：**Java interface ≈ Protocol + 你必须显式 implements；Python Protocol = "像就行"，显式继承是可选加冕。** 项目对"外部可替换的接缝"用 Protocol（估算器），对"内部家族"用 ABC（规则基类）——选择标准是：**要不要强制同族？** 估算器希望"任何形状对的实现都能插进来"（结构化）；规则希望"兄弟共享模板、明确属于这个家族"（名义）。

## 7.5 模型路由：贵的 token 用在刀刃上

`llm/router.py` 定义任务分级：

```python
class TaskKind(str, Enum):
    """LLM 调用任务类型。"""

    DECISION = "decision"  # ReAct 循环内的工具决策
    COMPRESS = "compress"  # 记忆压缩摘要
    REVIEW = "review"      # 深度审查 / 最终总结
    JUDGE = "judge"        # 评测打分（LLM-as-judge）
```

但实际路由只分两支：

```python
    def model_for(self, kind: TaskKind) -> str:
        # 压缩摘要属于轻任务；工具决策与深度审查共享同一次推理调用
        # （模型在决策的同时就在分析代码），必须用强模型保证审查质量。
        if kind == TaskKind.COMPRESS:
            return self._settings.model_fast
        return self._settings.model_strong
```

| 任务 | 实际路由 | 理由 |
|---|---|---|
| 压缩（COMPRESS） | 快模型，temperature=0.0 | 轻任务：消息列表摘成要点 |
| 审查与工具决策（REVIEW） | 强模型 | **一次调用同时承担决策与分析**——模型决定调什么工具时就在读代码分析，降级会直接伤审查质量 |
| `DECISION` 枚举值 | **当前没有单独使用** | 与 REVIEW 合并为同一次调用 |
| `JUDGE` 枚举值 | **当前没有使用** | 为"评测环节的 LLM-as-judge"预留；当前评测用确定性软匹配（第 09 篇） |

后两行是诚实清单的常客：**枚举定义了四个任务，实际路由场景只有两个**。`DECISION` 的合并原因写在注释里（架构决定了"决策即分析"）；`JUDGE` 是明确的预留。这类"设计意图 vs 实际使用"的差异，读代码时如实标注比假装完整更重要。

## 7.6 LLM 客户端：薄封装的三个约定

`llm/client.py` 是 openai SDK 的薄封装，模块头列出三个约定：

```python
"""LLM 客户端：openai SDK 的薄封装（OpenAI 兼容协议）。

设计要点：
- 只暴露 CodeSage 自己的 LLMResponse / ToolCall 数据结构，
  openai SDK 的类型不泄漏到 Agent 层（可测试、可替换）；
- 消息格式直接使用 OpenAI 的 dict 协议（role/content/tool_calls/tool_call_id），
  因为自研 ReAct 循环原样维护它，无需转换；
- 惰性初始化底层 client，未配置 API Key 时给出明确错误。
"""
```

### 约定一：SDK 类型不外泄

对外只有三个自有结构（都是 dataclass）：

| 结构 | 字段 | 说明 |
|---|---|---|
| `ToolCall` | `id` / `name` / `arguments`(dict) / `raw_arguments`(str) / `parse_error` | 模型请求的一次工具调用 |
| `LLMResponse` | `content` / `tool_calls` / `usage` / `model` + `has_tool_calls` 属性 | 统一响应 |
| `TokenUsage` | `input_tokens` / `output_tokens` / `calls` | 用量（第 01 篇） |

循环、Mock、测试都只认这些类型——openai SDK 的对象形状变化被挡在模块内部一层。**"可测试"的底座**：`MockLLMClient` 能替换真客户端，因为循环依赖的接口只有 `chat()` 一个方法。

### 约定二：参数解析失败静默消化

模型返回的 `tool_calls` 参数是 JSON 字符串，可能格式出错。这里不抛异常——把失败记在 `ToolCall.parse_error` 里、参数留空：

```python
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    arguments = parsed
                else:
                    parse_error = f"参数不是 JSON 对象：{raw[:120]}"
            except json.JSONDecodeError as exc:
                parse_error = f"参数 JSON 解析失败：{exc}"
```

参数为空的后果：工具执行时缺必填参数 → `ERROR: 参数不匹配…` → 模型下一轮自纠。**又一处"错误是可回填的信息"。**

### 约定三：惰性初始化 + 明确错误

```python
    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.configured:
            raise LLMNotConfiguredError(
                "未配置 LLM API Key。请复制 .env.example 为 .env 并填入密钥，"
                "或使用 `codesage review --no-llm` 运行纯确定性分析。"
            )
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise LLMError("缺少依赖 openai，请先执行 pip install -e .") from exc
        self._client = OpenAI(
            api_key=self._settings.api_key,
            base_url=self._settings.base_url,
            timeout=self._settings.timeout,
            max_retries=2,  # SDK 自带指数退避重试
        )
        return self._client
```

- **惰性**：构造 `LLMClient` 时什么都不做，第一次 `chat()` 才真正建 SDK 客户端（`_ensure_client` 三行判断：已有 → 直返；没配 key → 明确错误；否则建）。好处：`pipeline` 可以放心构造它，不配 key 也不会有副作用；
- **明确错误**：`LLMNotConfiguredError` 的文案给出两条出路（配密钥 / 用 `--no-llm`）——错误处理的最好形式是"告诉用户下一步做什么"；
- **`max_retries=2`**：HTTP 重试这种"标准且无优化空间"的能力用 SDK 自带的（注释写明"SDK 自带指数退避重试"）。

### 消息协议：进出各一半

**发送侧**——`chat()` 把消息原样交给 SDK（`messages` 就是 OpenAI 的 dict 协议），失败统一包装：

```python
        try:
            resp = client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - 统一包装为 LLMError
            raise LLMError(f"LLM 调用失败：{exc}") from exc
```

（`raise ... from exc` 语法见本节末加餐。）

**接收侧**——把 SDK 响应翻译成自有结构；`tool_calls` 逐个转换（就是上面的 parse_error 逻辑）。**回填侧**有两个自由的构造函数，循环用它把响应写回记忆：

```python
def assistant_message_with_tool_calls(resp: LLMResponse) -> dict[str, Any]:
    """把 LLMResponse 中的 tool_calls 转成 OpenAI 协议消息（用于回填历史）。"""
    return {
        "role": "assistant",
        "content": resp.content or None,
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": tc.raw_arguments or "{}"},
            }
            for tc in resp.tool_calls
        ],
    }


def tool_result_message(tool_call_id: str, content: str) -> dict[str, Any]:
    """构造工具结果消息（role=tool）。"""
    return {"role": "tool", "tool_call_id": tool_call_id, "content": content}
```

到这里，**消息序列的全家福**可以拼出来了——`memory.build()` 产出的列表长这样：

```python
[
  {"role": "system", "content": "<BASE_SYSTEM_PROMPT + 聚焦 + 项目背景 + 经验 + 资源>"},   # 冻结区
  {"role": "user",   "content": "<任务简报>"},                                             # 冻结区
  {"role": "system", "content": "## 历史审查过程摘要…"},                                   # 压缩区（压缩后出现）
  {"role": "assistant", "content": None, "tool_calls": [...]},                             # 活跃区：一轮开始
  {"role": "tool", "tool_call_id": "call_xxx", "content": "..."},                          # 活跃区：工具结果
  ...
]
```

**三种角色、一套协议、四个来源**——这就是自研循环原样维护的"对话"。

### MockLLMClient：一个类管测试

```python
class MockLLMClient:
    """脚本化 Mock 客户端：按预置响应序列返回，用尽后返回空响应。

    用途：
    - 单元测试（不依赖网络与 API Key）
    - `codesage review --mock` 演示模式
    """
```

它按"预置响应队列"返回（`self._responses.pop(0)`），越界后给一个固定的"（mock 响应已用尽）"；并记录每次请求的完整消息快照：

```python
        self.calls.append([dict(m) for m in messages])
```

`calls` 让测试可以断言"第二轮请求里有没有带上 ERROR 消息""压缩后请求的消息序列长什么样"。**诚实标注**：类 docstring 里写的"`codesage review --mock` 演示模式"**当前并不存在**——`review` 命令没有 `--mock` 选项（`eval --mock` 走的是 `use_llm=False` 的真实确定性链路，不是这个类）。Mock 目前只被单元测试使用。这条"文档与代码的漂移"记在第 12 篇诚实清单。

> ### 【Python 加餐 25】异常链与自定义异常（Java 对照）
>
> `raise LLMError(f"LLM 调用失败：{exc}") from exc`——末尾的 `from exc` 是**异常链**语法，对应 Java 的 `new LLMError(msg, cause)`：
>
> ```python
> try:
>     resp = client.chat.completions.create(**kwargs)
> except Exception as exc:
>     raise LLMError(f"LLM 调用失败：{exc}") from exc   # 保留原始异常为 __cause__
> ```
>
> 效果：异常追溯时两层都在——"业务层是 LLMError，根因是 ConnectionTimeout"。**用 `from` 显式链接 vs 不用**的区别：不用时 Python 3 遇到"在处理异常时又抛新异常"也会自动挂上 `__context__`（"During handling of the above exception…"），但 `from` 表达的是"**这是它的直接原因**"，语义更强。读 traceback 时看到 `The above exception was the direct cause of the following exception` 就是 `from` 的效果。
>
> **为什么这里必须包装**：循环只 `except LLMError`（第 05 篇）——不包就无法统一处理；包装又必须保留根因——不保留就丢失了调试信息。**"对外统一、对内保留"**是异常设计的基本盘。
>
> 项目在 `core/errors.py` 有一套九类异常体系：`CodeSageError`（基类）→ `ConfigError` / `LLMError` / `BudgetExceededError` / `ToolExecutionError` / `IngestError` / `HarnessError`，其中 `LLMError` 下面再分 `LLMNotConfiguredError`。层次和 Java 异常树同构。**一个值得对比的细节**：Python 异常没有 checked/unchecked 之分（加餐 12 讲过），所以这套体系的强制力全靠约定——"**内部模块抛语义化异常**（如 LLMNotConfiguredError），而不是裸 Exception"（`errors.py` 头注释原话），CLI 层按类别统一捕获转友好提示。
>
> 最后一条实用知识：捕获可以用父类。`except LLMError` 会把 `LLMNotConfiguredError` 一并接住（子类关系）——**捕获子类永远在父类之前**（Java 同规矩），否则子类分支永远不可达。

## 7.7 小结与下一站

- **三区记忆**：冻结区（永不改动、可命中缓存）+ 压缩区（一个 system 角色的摘要字符串）+ 活跃区（最近 4 轮原文）；`build()` 固定为"冻结 + 摘要 + 活跃"。
- **压缩**：触发在 loop、执行在 memory；`_find_keep_index` 的**切点必须对齐 assistant**（协议配对）；序列化防二次膨胀（600/900 截断）；压缩 prompt 白名单保留 + 滚雪球合并；失败保护（"信息优先于成本"）；压缩器 = 快模型 + 零温度 + 同账本记账。
- **预算**：OK/SOFT/HARD/ABORT 四态，60%/80%/95% 三闸；熔断是"不完整但可用优于完整但崩溃"；`record()` 记真实用量、`cost_yuan()` 出成本。
- **估算器**：CJK 1:1、其余 3.5:1、消息开销 +4/条 +3 基线；tiktoken 可插拔、失败静默回退；±20% 精度够用的原因是阈值安全垫。
- **路由**：只有压缩走快模型（决策与审查共享一次强模型调用）；`DECISION`/`JUDGE` 为定义未用（诚实标注）。
- **客户端**：三约定（类型不外泄、解析失败静默、惰性初始化）；消息协议三种角色全貌；`MockLLMClient` 只用于测试（docstring 漂移已标注）。
- **Python 加餐**：切片（★）、Protocol/结构化类型（vs Java interface）、异常链 `raise from`。

下一站进入**阶段四·五**——循环结束了（不论哪种出口），`det_findings`、`llm_findings`、`dismissed` 三个账本摆在桌上：驳回过滤、跨来源去重、评分、落盘、渲染成报告。整条流水线收网的时刻到了。

# 《CodeSage项目》——第 3 站 · 阶段二：确定性分析

> 📍 **主线进度**：起点 ✅ → 扫描 ✅ → **▶ 确定性分析（本篇）** → 组装 → 循环 → 工具 → 后勤 → 合并与输出

源码已经读进内存，接下来是**零 Token 的第一网**：13 条自研 AST 规则 + 3 个外部分析器。这一阶段产出的"候选发现"会被写进任务简报送给模型复核——它是整个"确定性层 + LLM 层"分工里零成本的那一半。本篇会比较长，因为这里藏着项目最大的单文件（631 行）和最多的设计细节。

## 3.1 阶段代码：一个引擎 + 三个分析器

`run_review` 的阶段二代码：

```python
    # ---------------------------------------------------------- 2. 确定性分析
    det_findings: list[Finding] = []
    engine = RuleEngine()
    analyzers = [a for a in default_analyzers() if a.is_available()]
    file_inventory: list[tuple[str, int]] = []

    # 一次读取全部源码（多处复用）        ← 备料段，第 02 篇 2.5 已讲
    ...

    # 自研规则引擎（零 Token，逐文件遍历）
    for rel, source in sources:
        det_findings.extend(engine.run(rel, source))

    # 外部分析器：支持批量的走单次子进程调用（大仓库性能关键路径）
    # 传入 base_dir 让分析器直接扫描真实目录（避免临时目录的写删开销）
    for analyzer in analyzers:
        if getattr(analyzer, "supports_batch", False):
            batch = analyzer.run_batch(sources, base_dir=workspace)
            for findings in batch.values():
                det_findings.extend(findings)
        else:
            for rel, source in sources:
                det_findings.extend(analyzer.run(rel, source))

    _emit(
        options,
        "static_done",
        {"findings": len(det_findings), "analyzers": [a.name for a in analyzers]},
    )
```

四个要点：

1. **`default_analyzers() if a.is_available()`**——外部分析器没安装就优雅跳过（容错设计之一）。你在只装了 Ruff 的机器上跑，审查照样进行，只是少了另外两网的发现。
2. **自研规则逐文件跑**：`engine.run(rel, source)` 一次处理一个文件，返回该文件的发现列表。
3. **外部分析器分两条路**：声明了 `supports_batch = True` 的（Ruff、Bandit）走 `run_batch`——**一次子进程调用处理全部文件**；其余（radon）逐文件调用。这是第 11 篇 44 倍提速的主角之一。
4. **统一出口**：无论来自自研规则还是外部工具，产物都是 `Finding`——本阶段结束时，`det_findings` 里躺着的是"候选发现"（candidate）。"候选"的含义：高置信度的可以直接进报告，低置信度的要等 Agent 复核。

## 3.2 `RuleEngine`：13 条规则的组织方式

`analyzers/ast_rules.py` 是项目最大的单文件（631 行），头注释交代了三条设计原则：

```python
"""自研 AST 规则引擎（零 Token 的核心资产）。

设计说明：
- 每条规则实现 `check(tree, ctx) -> list[Finding]`，可独立测试（正反例成对）；
- NodeRule 基类提供"节点类型 + 单节点检查"的模板，覆盖大多数规则；
- RuleContext 封装源码行与 Finding 构造，规则实现只关心检测逻辑；
- 规则内部异常被捕获并跳过——一条坏规则不能拖垮整个审查。

规则产出的是"候选发现"：高确定性规则（如裸 except）可直接进入报告；
启发式规则（如未使用 with）标记较低 confidence，由 LLM 复核确认。
"""
```

整个模块由**四个零件**组成：`RuleContext`（规则上下文）、`Rule`（规则基类）、`NodeRule`（按节点类型的模板基类）、`RuleEngine`（引擎）。

### 零件一：`RuleContext`——规则干活的工位

```python
@dataclass
class RuleContext:
    """规则执行上下文。"""

    filename: str
    source_lines: list[str] = field(default_factory=list)

    def line_text(self, lineno: int) -> str:
        if 1 <= lineno <= len(self.source_lines):
            return self.source_lines[lineno - 1].strip()
        return ""
```

两个字段：文件名、按行切好的源码（构造时 `source.splitlines()`）。`line_text(n)` 取第 n 行——**行号是 1-based，列表下标是 0-based**，所以 `source_lines[lineno - 1]`，越界返回空串。

更关键的是它封装了 Finding 的构造：

```python
    def make_finding(
        self,
        node: ast.AST,
        *,
        rule_id: str,
        title: str,
        description: str = "",
        suggestion: str = "",
        severity: Severity = Severity.MINOR,
        category: Category = Category.QUALITY,
        confidence: float = 1.0,
        include_evidence: bool = True,
    ) -> Finding:
        lineno = int(getattr(node, "lineno", 1) or 1)
        endline = int(getattr(node, "end_lineno", lineno) or lineno)
        evidence = self.line_text(lineno) if include_evidence else ""
        return Finding(
            file=self.filename,
            line_start=lineno,
            line_end=max(lineno, endline),
            category=category,
            severity=severity,
            title=title,
            description=description,
            suggestion=suggestion,
            evidence=evidence[:200],
            confidence=confidence,
            source=Source.AST_RULE,
            rule_id=rule_id,
        )
```

规则实现只需要说"哪条规则、什么标题、怎么修、多严重"，`make_finding` 负责其余字段。两个技术点：

- **行号直接从 AST 节点取**（`node.lineno` / `node.end_lineno`）——所以规则报出来的位置天然精确，不存在 LLM 那种"行号幻觉"。`getattr(node, "lineno", 1) or 1` 是防御式取值：某些节点没有行号属性（默认 1），属性值又可能是 None（`or 1` 兜底）。
- **`*` 分隔符**——`self, node` 之后的参数全部变成"仅关键字参数"（keyword-only）。这是刻意的：调用时 `ctx.make_finding(node, "CG001", ...)` 这种"位置传参错位"被语法层面禁止，必须写 `rule_id="CG001"`。十三个参数的长调用，强制命名让可读性和安全性都上来了。

### 零件二：`Rule` 与 `NodeRule`——两种规则写法

基类很薄：

```python
class Rule(ABC):
    """规则基类。"""

    id: str = "rule"
    title: str = ""

    @abstractmethod
    def check(self, tree: ast.AST, ctx: RuleContext) -> list[Finding]:
        """检查语法树，返回发现列表。"""
```

`NodeRule` 是给"逐节点检查"类规则准备的**模板方法**：

```python
class NodeRule(Rule):
    """按节点类型遍历的规则模板。

    子类声明 node_types 并实现 visit_node(node, ctx)，
    引擎负责遍历与分发。若规则需要整树上下文（如 parent 链），
    可直接重写 check()。
    """

    node_types: tuple[type, ...] = ()

    @abstractmethod
    def visit_node(self, node: ast.AST, ctx: RuleContext) -> list[Finding]:
        """检查单个节点。"""

    def check(self, tree: ast.AST, ctx: RuleContext) -> list[Finding]:
        findings: list[Finding] = []
        for node in ast.walk(tree):
            if isinstance(node, self.node_types):
                findings.extend(self.visit_node(node, ctx))
        return findings
```

子类只声明"关心哪几种节点"（`node_types`）和"单个节点怎么查"（`visit_node`），遍历和分发由基类完成。比如裸 except 规则的全部声明就是：

```python
class BareExceptRule(NodeRule):
    """裸 except：会吞掉 KeyboardInterrupt / SystemExit 等，且掩盖真实错误。"""

    id = "CG002"
    title = "裸 except"
    node_types = (ast.ExceptHandler,)
```

**逃生口**在注释里：需要整树上下文的规则（比如"open 调用在不在 with 里"，得沿父节点上溯）直接重写 `check()`——CG005 就是这么干的。**模板管合适的 80%，剩下的 20% 走原路**，这是模板方法模式的标准姿势。

### 零件三：两处工具函数

```python
def _build_parent_map(tree: ast.AST) -> dict[int, ast.AST]:
    """构建子节点 -> 父节点映射（用 id() 作为键，避免节点不可哈希问题）。"""
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    return parents
```

`ast.walk` 自上而下遍历所有节点，`iter_child_nodes` 取直接子节点——一趟下来得到"每个节点 → 它的父节点"的字典。**键用 `id(child)`（对象身份）而不是节点本身**：AST 节点对象不可哈希（`ast.AST` 没实现稳定的 `__hash__` 语义）。这个细节注释里写明了。

```python
def _is_string_like(node: ast.AST) -> bool:
    """判断表达式是否涉及字符串字面量。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _is_string_like(node.left) or _is_string_like(node.right)
    if isinstance(node, ast.JoinedStr):  # f-string
        return True
    return False
```

递归判断"这个表达式像不像字符串"：字符串常量、f-string（`ast.JoinedStr`）、或者两边拼接里任意一边像字符串。CG007（循环内字符串拼接）用它区分 `text += 1`（数字累加，正常）和 `text += "x"`（字符串拼接，可疑）。

### 零件四：`RuleEngine`——单次解析，多规则执行

```python
class RuleEngine:
    """规则引擎：单次解析，多规则执行。"""

    def __init__(self, rules: list[Rule] | None = None) -> None:
        self.rules = rules if rules is not None else default_rules()

    def run(self, filename: str, source: str) -> list[Finding]:
        """解析源码并执行全部规则。

        - 语法错误：报告一条 MAJOR（bug 类），后续规则无法执行；
        - 单条规则异常：跳过该规则（不影响其他规则）。
        """
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            return [
                Finding(
                    file=filename,
                    line_start=exc.lineno or 1,
                    line_end=exc.lineno or 1,
                    category=Category.BUG,
                    severity=Severity.MAJOR,
                    title=f"语法错误：{exc.msg}",
                    description=f"文件无法解析（{exc.__class__.__name__}），请先修复语法。",
                    source=Source.AST_RULE,
                    rule_id="CG000",
                )
            ]

        ctx = RuleContext(filename=filename, source_lines=source.splitlines())
        findings: list[Finding] = []
        for rule in self.rules:
            try:
                findings.extend(rule.check(tree, ctx))
            except Exception:  # noqa: BLE001 - 单条规则失败不影响整体
                continue
        return findings
```

三个设计点：

- **`ast.parse(source)` 只调一次**：13 条规则共用同一棵语法树，各自 `ast.walk` 遍历。如果每条规则重新 parse，性能会差好几倍——"单次解析多规则执行"是这个类存在的核心理由。
- **语法错误是特殊发现（CG000）**：文件 parse 不了，13 条规则全都无法执行，直接返回一条 MAJOR 级"语法错误"发现（带出错行号）。这也解释了为什么它会出现在报告里——一个语法错误的文件本身就是最高优先级的问题。
- **规则隔离**：任何一条规则内部抛异常，`except Exception: continue` 直接跳过它，其余 12 条照常跑。**这是"13 条规则独立开发、独立测试"能成立的前提**——一条规则的 bug 不会拖垮整场审查。

> ### 【Python 加餐 10】类、继承与抽象基类（Java 对照）
>
> Python 的类和 Java 的类在"怎么写"上像，在"约束有多强"上差别很大。
>
> **继承语法**：`class NodeRule(Rule):` ——括号里写父类，相当于 `extends`。多继承就直接列多个（`class C(A, B)`），没有 `implements` 关键字——**Python 不区分"类继承"和"接口实现"，全靠约定**：想表达"接口"就用 ABC（见下），想表达"混入"就多继承。
>
> **`ABC` 与 `@abstractmethod`**：
>
> ```python
> class Rule(ABC):
>     @abstractmethod
>     def check(self, tree, ctx) -> list[Finding]: ...
> ```
>
> `ABC` 是标准库 `abc` 模块的抽象基类（约等于 Java 的 `abstract class`），`@abstractmethod` 标记的方法**子类不实现就不能实例化**（Python 在实例化时检查，Java 在编译时检查——这是"运行时 vs 编译时"的又一次体现）。注意 `Rule.check` 的方法体是 `...`（Ellipsis，占位符）——抽象方法可以不给实现，和 Java 的 `abstract` 方法一致。
>
> **`super()` 与显式父类调用**：NodeRule 没有显式调 `super()`，因为它重写 `check` 时是**完全替换**而非"扩展"。需要扩展时写 `super().check(tree, ctx)`（Python 3 不用传类名和 self，比 Java 的 `super.method()` 少些样板）。
>
> **`isinstance` 而不是 `instanceof`**：`isinstance(node, ast.ExceptHandler)` ——注意它在**运行时**检查对象类型（鸭子类型世界的通行证），而 `node_types = (ast.ExceptHandler,)` 这种**类属性**也是普通值：Python 的类属性、方法、注解全是运行时可读的普通对象，没有 Java 的"元数据 + 反射才能拿到"的隔阂。
>
> **一个 Java 程序员会不习惯的点**：Python 没有 `private`。约定是**下划线开头表示"内部使用"**（`_build_parent_map`、`_is_string_like`）——IDE 有时会警告，但语言层面不拦。这不是妥协，而是靠约定 + 代码评审 + 测试来守边界。

> ### 【Python 加餐 11】推导式全家桶（Java 对照）
>
> **推导式（comprehension）是 Python 的招牌语法**：用一个表达式完成"遍历 + 过滤 + 变换 + 收集"，对应 Java 的 Stream 链。对照看最清楚：
>
> ```java
> // Java：从 targets 里挑出 Name 类型的，取 id 收集成列表
> node.targets.stream()
>     .filter(t -> t instanceof Name)
>     .map(t -> ((Name) t).id)
>     .collect(Collectors.toList());
> ```
>
> ```python
> # Python 推导式（本项目 CG012 规则里的真实代码）
> [t.id for t in node.targets if isinstance(t, ast.Name)]
> ```
>
> 读法是"**表达式 for 变量 in 可迭代对象 if 条件**"——先说要什么（`t.id`），再说从哪来（`for t in ...`），最后说筛什么（`if ...`）。四种形态：
>
> ```python
> [x * 2 for x in nums if x > 0]          # 列表推导式 → list
> {t.value: 0 for t in Severity}           # 字典推导式 → dict（models.py 按严重级计数）
> {f.source for f in group}                # 集合推导式 → set（dedup 去重时统计"有几种来源"）
> (x * 2 for x in nums)                    # 生成器表达式 → 惰性求值，不建列表
> ```
>
> 集合推导式那个例子（`sources = {f.source for f in group}`）特别能说明问题：一行拿到"这组发现涉及哪些来源"，后续 `len(sources) == 1` 判断"单一来源"。Java 得 `group.stream().map(f -> f.source).collect(Collectors.toSet())`。
>
> **生成器表达式**是"穷人家的流"：`any(k.lower() in haystack for k in case.keywords)` 不会为 keywords 建中间列表，边算边问，命中即停（第 09 篇的软匹配会用到）。**性能提示**：推导式比"for 循环 + append"快（解释器层专门优化过），但可读性优先——超过两个 `for` 嵌套就该退回普通循环了。本项目的用法都很克制：全部是"单层 for + 一个 if"。

## 3.3 13 条规则逐条看

### 全表（收藏级）

`default_rules()` 返回全部 13 条，覆盖四个维度（最佳实践靠 CG009 一条撑起）：

| ID | 规则名 | 维度 | 严重级 | 置信度 | 检测机制 |
|---|---|---|---|---|---|
| CG001 | 可变默认参数 | bug | MAJOR | 1.0 | 函数默认值是 List/Dict/Set 字面量、推导式或 `list()/dict()/set()` 工厂调用 |
| CG002 | 裸 except | bug | MAJOR | 1.0 | `ExceptHandler` 的 `type is None` |
| CG003 | 异常被静默吞没 | bug | MAJOR | 1.0 | except 块体只有一条 `pass` |
| CG004 | `== None` 比较 | quality | MINOR | 1.0 | `Compare` 中 Eq/NotEq 的操作数是 None 常量 |
| CG005 | open() 未使用 with | bug | MINOR | **0.6** | open 调用沿父链上溯，不在 With/AsyncWith 内 |
| CG006 | 缺少 docstring | quality | MINOR / INFO | 0.9 | 公共符号 MINOR、私有符号 INFO；魔术方法与测试文件豁免 |
| CG007 | 循环内字符串拼接 | performance | MINOR | 0.8 | For/While 体内 `+=` 的右值是字符串字面量或 f-string |
| CG008 | 列表成员检查 | performance | INFO | 0.85 | `in`/`not in` 的右侧是 ≥3 元素的 List/Tuple 字面量 |
| CG009 | range(len(...)) 迭代 | best_practice | INFO | 0.95 | `for i in range(len(x))`；按是否用下标给不同建议 |
| CG010 | 过宽的 except Exception | quality | MINOR | 0.7 | 捕获类型恰为 `Exception` |
| CG011 | eval / exec 使用 | security | MAJOR | 1.0 | 调用名为 `eval`/`exec` 的 Name |
| CG012 | 硬编码敏感信息 | security | MAJOR | 0.75 | 变量名含密钥关键词 + 字符串值像真密钥（见下） |
| CG013 | 命令注入风险 | security | MAJOR | 0.9~1.0 | `os.system`/`os.popen`（1.0），或 `subprocess` 带 `shell=True`（0.9） |

**置信度不是装饰**——它标明"这条发现是候选还是结论"：

- **1.0 / 0.95 / 0.9 档**（形态明确、几乎不误报）：裸 except、`== None`、eval 调用、缺 docstring；
- **0.7~0.85 档**（形态可疑、需结合上下文）：循环内拼接在小循环里无所谓、顶层 `except Exception` 是合理兜底；置信度留出复核余地；
- **0.6 档**（CG005 独享）："open 不在 with 里"距离"资源泄漏"还差一个语义判断（调用方有没有手动 close），**必须**经过复核。

### 深读一：CG001——"可变默认参数"要覆盖多少种写法

```python
class MutableDefaultArgRule(NodeRule):
    """可变默认参数：def f(x=[]) —— 经典陷阱，默认值在函数定义时只求值一次。"""

    id = "CG001"
    title = "可变默认参数"
    node_types = (ast.FunctionDef, ast.AsyncFunctionDef)

    _MUTABLE_LITERAL = (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp)
    _MUTABLE_FACTORY = ("list", "dict", "set")

    def visit_node(self, node: ast.AST, ctx: RuleContext) -> list[Finding]:
        defaults = list(node.args.defaults) + [
            d for d in node.args.kw_defaults if d is not None
        ]
        findings: list[Finding] = []
        for default in defaults:
            is_mutable = isinstance(default, self._MUTABLE_LITERAL)
            if not is_mutable and isinstance(default, ast.Call):
                if isinstance(default.func, ast.Name) and default.func.id in self._MUTABLE_FACTORY:
                    is_mutable = True
            if is_mutable:
                ...
```

覆盖面是这条规则的功力所在：

- **参数分两组**：普通参数默认值在 `node.args.defaults`，仅关键字参数默认值在 `node.args.kw_defaults`（该列表可能含 None，先过滤）；
- **六种字面量形态**：`[]`、`{}`、集合字面量的三种等价写法（`{1, 2}` 是 Set，`{k: v}` 是 Dict，`{x for x in ...}` 是 SetComp……三种推导式全列上了）；
- **工厂调用**：`x=list()`、`x=dict()`、`x=set()`——`set()` 没有字面量语法，必须靠这组名字兜底。

修复建议的文案也是规则资产的一部分："改用 None 作默认值，函数内部按需创建：`if x is None: x = []`"。这个陷阱的原理——为什么共享默认值会出事——放在【Python 加餐 13】彻底讲。

### 深读二：CG005——一条规则的置信度为什么是 0.6

这条规则是全项目最能说明"规则与 LLM 分工"的标本。类注释：

```python
class UnclosedOpenRule(Rule):
    """open() 未使用 with：资源泄漏候选（启用 LLM 复核的典型场景）。

    该规则只能发现"未在 with 内"的 open 调用；
    调用方是否手动 close 需要语义确认，因此置信度设为 0.6，
    由 Agent 的 LLM 环节复核后决定保留或丢弃。
    """
```

它的检测不走 `NodeRule` 模板（需要父链），重写 `check()`：

```python
    def check(self, tree: ast.AST, ctx: RuleContext) -> list[Finding]:
        parents = _build_parent_map(tree)
        findings: list[Finding] = []
        for node in ast.walk(tree):
            if not self._is_open_call(node):
                continue
            if self._inside_with(node, parents):
                continue
            findings.append(...)
```

`_is_open_call` 判断"是不是名为 `open` 的普通函数调用"；`_inside_with` 沿父链一层层上溯：

```python
    @staticmethod
    def _inside_with(node: ast.AST, parents: dict[int, ast.AST]) -> bool:
        cur: ast.AST | None = node
        while cur is not None and id(cur) in parents:
            parent = parents[id(cur)]
            if isinstance(parent, (ast.With, ast.AsyncWith)):
                return True
            cur = parent
        return False
```

发现文案里主动写了"（若已手动 close，请在复核时忽略本条）"——**把判断权交给下一道工序**。这些 0.6 的候选进入 Agent 后有两种归宿：模型读了周边代码确认漏了 close → 重新提交；确认有手动 close → 显式驳回（第 06 篇的两个通道）。

### 深读三：CG012——怎么让"硬编码密钥"不成为噪音机器

朴素做法"变量名里有 password 就报"会打出一堆占位符。CG012 用**双重条件**：名字要像，值也要像。

```python
    _HINTS = ("password", "passwd", "secret", "token", "api_key", "apikey", "access_key")
    _PLACEHOLDERS = {"password", "secret", "token", "changeme", "example", "placeholder", "xxx"}
```

值的四道门：长度 ≥ 6、不在占位符白名单、不以常见示例前缀开头：

```python
        text = value.value.strip()
        if len(text) < 6:
            return []
        if text.lower() in self._PLACEHOLDERS or text.lower().startswith(("your", "test", "<")):
            return []
```

于是 `API_KEY = "your-key-here"`、`token = "xxx"`、`password = "changeme"` 全部静默放过——**这些在示例和教学代码里出现得太频繁，误报它们等于给工具刷负分**。置信度 0.75 也是同理：名字和值都像，仍可能是无害的公开测试 token，留一点复核余地。

顺带看 CG009 的一个小心思——检测到 `for i in range(len(x))` 后，先看循环体里**有没有用下标**，再决定建议话术：

```python
        suggestion = (
            "需要索引和元素：改用 `for i, item in enumerate(x):`"
            if uses_index
            else "只需要元素：直接 `for item in x:` 迭代"
        )
```

同样一条检测，建议分两种场景——"建议必须可执行"这条守则在规则层的提前落地。

> ### 【Python 加餐 12】异常处理体系（Java 对照）
>
> 本项目里异常处理出现得非常密集，正好一次讲完。
>
> **基本形态几乎逐字对应**：
>
> ```python
> try:
>     tree = ast.parse(source)
> except SyntaxError as exc:        # 捕获特定异常，绑定到 exc
>     ...
> except Exception:                  # 兜底（尽量别裸用——见下）
>     ...
> ```
>
> `except X as exc` ≈ Java 的 `catch (X exc)`；**多重捕获**用元组：`except (TypeError, ValueError):`（≈ Java 的 multi-catch `catch (A | B e)`）。
>
> **最大的差异：Python 没有 checked exception。** Java 的 `throws IOException` 逼你处理或声明；Python 里**所有异常都是 unchecked**——函数永远不会声明"我可能抛什么"（类型注解语法上支持 `raises` 但没人用，工具才看）。好处是代码干净，坏处是"这个函数会不会抛"要靠文档和直觉。项目的对策是**自定义异常 + 语义化命名**（`core/errors.py`）：
>
> ```python
> class CodeSageError(Exception): ...
> class LLMError(CodeSageError): ...
> class LLMNotConfiguredError(LLMError): ...
> ```
>
> 层次结构和 Java 一样（基类 + 细分），好处也一样：`except LLMError` 一网打尽"LLM 相关的一切失败"，CLI 层统一捕获转成友好提示。
>
> **`else` 和 `finally`**：`try/except/else/finally` 四段齐全——`else` 是"没抛异常才执行"，`finally` 是"无论如何都执行"（基本同 Java）。项目里 `finally` 的典型用法是关连接：`try: ... finally: store.close()`。
>
> **裸 except 为什么危险（CG002 检测的就是它）**：`except:` 会连 `KeyboardInterrupt`（Ctrl+C）和 `SystemExit` 都吞掉——程序想退出都退不掉，且真实错误被无声掩盖。守则：捕获具体类型；确需兜底用 `except Exception:` 并记录日志。
>
> **`# noqa: BLE001` 是什么**：你在引擎代码里见过 `except Exception:  # noqa: BLE001 - 单条规则失败不影响整体`。`noqa` 是给 Ruff 的注释指令——"这一行故意忽略 BLE001（broad-except）告警"，破折号后是**给人类读的理由**。相当于 Java 的 `@SuppressWarnings("...")`，但要求你把理由写在旁边。

> ### 【Python 加餐 13】可变与不可变对象——Python 最深的坑（Java 对照）★
>
> 这是 Java 转 Python 最容易翻车的地方，也是 CG001 规则存在的理由。
>
> **第一层：对象分可变与不可变。**
>
> | 不可变（immutable） | 可变（mutable） |
> |---|---|
> | `int`、`float`、`bool`、`str`、`tuple`、`frozenset` | `list`、`dict`、`set`、自定义对象 |
>
> 不可变对象创建后内容永不改变——`s = "abc"` 后任何"修改"都其实是**创建新对象**。这解释了 CG007 为什么报"循环内字符串拼接"：`text += row` 在循环里每次都造一个新字符串、复制旧的，复杂度 O(n²)——**Java 程序员会本能想到 StringBuilder，Python 的对应物是 `"".join(parts)` 或收集到列表**。
>
> **第二层：变量是"名字"，赋值不复制。** 和 Java 的对象引用一样，`b = a` 只是让两个名字指向同一个对象（对不可变对象无感，对可变对象是事故温床）：
>
> ```python
> a = [1, 2]
> b = a            # 不是复制！a 和 b 是同一个列表
> b.append(3)
> print(a)         # [1, 2, 3] —— a 也"被改了"
> ```
>
> 要复制得显式：`b = list(a)` / `b = a[:]`（浅拷贝）/ `import copy; copy.deepcopy(a)`（深拷贝）。Java 里 `List.copyOf` / 手工 new 的习惯在这里变成"默认共享引用，想隔离得主动"。
>
> **第三层：默认参数的经典陷阱（CG001 抓的就是它）：**
>
> ```python
> def append_to(x, target=[]):   # 危险！
>     target.append(x)
>     return target
>
> append_to(1)   # [1]
> append_to(2)   # [1, 2]  而不是 [2] ！
> ```
>
> **默认值 `[]` 只在函数定义时求值一次**（模块加载时），所有调用共享同一个列表对象。等价于 Java 里写了个 `static final List DEFAULT = new ArrayList();` 然后每个调用都往里塞——但在 Python 里这**是默认参数的常规写法**，所以格外阴险。正确姿势：
>
> ```python
> def append_to(x, target=None):
>     if target is None:
>         target = []
>     target.append(x)
>     return target
> ```
>
> 项目里两处应用能对上：配置的 `Field(default_factory=lambda: [...])`（每次调用工厂拿新列表）和 dataclass 的 `field(default_factory=list)`——加餐 5 里"为什么不能直接 `= []`"的答案就在这里。**记忆口诀：可变默认值一律用"None 哨兵 + 函数内新建"或 `default_factory`。**
>
> **第四层（进阶）：`+=` 对两种对象行为不同。** `x += [1]` 对列表是**原地修改**（`__iadd__`，等价 `extend`），对字符串/元组是重建。所以列表的 `+=` 有"隐藏的共享修改"；这条先埋着，第 08 篇去重的 `extend` 与第 05 篇的语句计数都会用到。

## 3.4 三个外部分析器：Ruff / Bandit / radon

自研规则只覆盖 13 种模式，生态轮子照用。三个分析器都实现同一个协议（`analyzers/base.py`）：

```python
class Analyzer(ABC):
    """确定性分析器协议。"""

    name: str = "analyzer"
    display: str = "分析器"
    supports_batch: bool = False  # True 时 pipeline 优先调用 run_batch（性能路径）

    @abstractmethod
    def is_available(self) -> bool:
        """工具是否可用（未安装时优雅跳过，不阻塞审查）。"""

    @abstractmethod
    def run(self, filename: str, source: str) -> list[Finding]:
        """分析源码，返回发现列表。filename 为相对路径（用于报告中显示）。"""
```

| 分析器 | 定位 | 调用方式 | 关键配置 |
|---|---|---|---|
| Ruff | 风格/命名/导入/潜在错误 | 子进程，JSON 输出 | `select = "E,W,F,I,N"`（不选 D——docstring 交给 CG006，避免重复报告） |
| Bandit | 安全扫描 | 子进程，JSON 输出 | 全量规则 + 高危规则严重级上调（见下） |
| radon | 圈复杂度 | **Python 库直接调用**（省一次子进程） | CC ≥ 10 报 MINOR、≥ 20 报 MAJOR |

### `find_tool`：不激活 venv 也能找到工具

找外部可执行文件（ruff.exe / bandit.exe）的顺序：**当前解释器所在目录 → PATH**：

```python
def find_tool(executable: str) -> str | None:
    """查找外部命令行工具，返回可执行文件路径。

    查找顺序（关键工程细节）：
    1. 当前解释器所在目录（venv/Scripts）——保证用项目声明的依赖版本，
       避免被系统全局安装的旧版本干扰（版本差异会改变输出格式）；
    2. PATH 环境变量 —— 回退到全局安装的工具。

    为什么需要第 1 步：不激活 venv 直接运行 codesage.exe 时，
    PATH 中不包含 venv/Scripts，Bandit 这类仅装在 venv 里的工具会找不到。
    """
    scripts_dir = Path(sys.executable).parent
    for name in (executable, f"{executable}.exe", f"{executable}.cmd"):
        candidate = scripts_dir / name
        if candidate.is_file():
            return str(candidate)
    return shutil.which(executable)
```

真实场景：CodeSage 装在虚拟环境里，Bandit 作为依赖也在同一个 venv。用户如果**没激活 venv**、直接用绝对路径跑 `codesage.exe`，`PATH` 里根本没有 `venv/Scripts`——`shutil.which` 找不到。从 `sys.executable`（正在运行的 Python）的同级目录找，一定能找到"兄弟工具"。附带好处：保证用的是项目声明的那份版本。

### 结果映射：三个世界，一种 Finding

三家工具的输出格式、词汇、置信度语义都不同，映射层各写各的。

**Ruff**：`--select` 限定五类规则集；F 码里有一个"明确逻辑问题"子集会被**升级到 MAJOR**：

```python
# F 规则中代表明确逻辑问题的子集（提升严重级）
_SEVERE_F_CODES = {
    "F821",  # undefined name
    "F811",  # redefinition of unused name
    "F823",  # local variable referenced before assignment
    ...
}
```

其余 F 码 MINOR、E/W 是 MINOR、N/I 是 INFO，统一置信度 0.9。**为什么要有这个子集**：`F821 未定义变量` 和 `E501 行太长` 若同为 P2，报告的可信度就崩了——严重级映射必须体现"这条到底有多要紧"。

**Bandit**：严重级/置信度双维度映射，另外有一张**按业务风险重映射**的高危规则表（本轮更新的重点）：

```python
# 按业务风险重映射的高危规则（对齐"工具视角"与"业务风险"）：
# 注入 / 任意代码执行 / 反序列化类漏洞即使被工具标为 MEDIUM，也应按高风险呈现——
# SQL 注入与"缺 docstring"同为 P2 是不可接受的呈现（用户反馈的真实案例）。
# 注：确定性层最高到 MAJOR（CRITICAL 留给 LLM 确认数据流可达性后升级提交）。
_ELEVATED_RULES: dict[str, Severity] = {
    "B608": Severity.MAJOR,  # SQL 注入（字符串构造查询）
    "B610": Severity.MAJOR,  # Django extra()/raw() SQL
    "B611": Severity.MAJOR,  # Django RawSQL
    "B307": Severity.MAJOR,  # eval()
    "B301": Severity.MAJOR,  # pickle 反序列化
    "B302": Severity.MAJOR,  # marshal 反序列化
    "B506": Severity.MAJOR,  # yaml.load（不安全加载）
}
```

```python
def _severity_for(test_id: str, sev_key: str) -> Severity:
    """严重级映射：工具标注 + 高危规则按风险上调（只升不降）。"""
    mapped = _SEVERITY_MAP.get(sev_key, Severity.INFO)
    elevated = _ELEVATED_RULES.get(test_id)
    if elevated is not None and elevated.weight > mapped.weight:
        return elevated
    return mapped
```

两条设计纪律：**只升不降**（映射永远朝"更严重"靠，安全类问题宁严勿宽）；**上限 MAJOR**（确定性层不飙到 P0——`CRITICAL` 留给"LLM 确认数据流可达"之后升级，比如确认了用户输入真的能流进那行 SQL）。这段注释还留下了完整的用户反馈链路："SQL 注入与缺 docstring 同为 P2 是不可接受的"——第 11 篇会把这类"反馈驱动的修复"汇成一节。

**radon**：以库形式调用，只报超阈值的生产代码函数：

```python
# 复杂度阈值（radon 惯例：>10 需要关注，>20 需要重构）
COMPLEXITY_MINOR = 10
COMPLEXITY_MAJOR = 20
```

### 批量模式：从"逐文件子进程"到"一次扫目录"

`supports_batch = True` 的 Ruff/Bandit 走 `run_batch(files, base_dir)`。接口里有两个参数：

```python
    def run_batch(
        self,
        files: list[tuple[str, str]],
        base_dir: Path | None = None,
    ) -> dict[str, list[Finding]]:
```

- `files`：（相对路径, 源码）列表——和 `sources` 同构；
- `base_dir`：**给了就直接扫描真实目录**（零临时文件），不给则回退"写入临时目录再扫"。

批量扫描的内部（以 Bandit 为例）：一次 `bandit -r <目录>`，然后把结果里的文件路径**还原成原始相对路径**并按已知清单过滤（`_to_relative`：先试相对路径，失败再按文件名兜底匹配）。两个性能设计的来龙去脉（为什么批量、为什么零临时文件）放在第 11 篇完整讲——这里先知道结论：**逐文件调 bandit 在 62 个文件的仓库上要 90 秒以上，批量模式只要 1.5 秒**。

> ### 【Python 加餐 14】subprocess 与 JSON（Java 对照）
>
> **调外部命令**用 `subprocess.run`——对应 Java 的 `ProcessBuilder`：
>
> ```python
> result = subprocess.run(
>     ["ruff", "check", "--output-format=json", "-"],
>     input=source,             # 以 stdin 喂入源码（Ruff 支持，Bandit 不支持）
>     capture_output=True,      # 捕获 stdout/stderr（≈ ProcessBuilder 的 redirectErrorStream + 读取）
>     text=True,                # 返回 str 而不是 bytes（省去手动 decode）
>     encoding="utf-8",
>     errors="ignore",          # 解码错误不抛异常
>     timeout=60,               # 超时秒数——超时抛 TimeoutExpired
>     check=False,              # 非零退出码不抛异常（我们要自己看结果）
> )
> result.stdout  # 标准输出文本
> result.returncode  # 退出码
> ```
>
> 三个 Java 差异点：**命令用列表传**（`["git", "diff", "HEAD"]`）而不是拼字符串——天然免疫命令注入，也免了引号处理；**没有 `&&` 链接**——要串联就在 Python 层写两个 run；**`check=True` 才是"非零就抛"**（默认 False 是"给我结果我自己判断"）。
>
> 项目里封装了一层 `run_command()`（`analyzers/base.py`）：统一 try/except 把任何失败（命令不存在、超时）变成"返回 None"，让调用方降级处理——又是那条"辅助设施失败不许拖垮主流程"。
>
> **JSON 处理**用标准库 `json`：
>
> ```python
> data = json.loads(text)              # 字符串 → dict/list（≈ Jackson readValue）
> text = json.dumps(obj, ensure_ascii=False)   # 对象 → 字符串
> ```
>
> `ensure_ascii=False` 是中文项目的必加项——默认 True 会把中文转义成 `\uXXXX`（存储没问题、人读不懂）。`json.loads` 失败抛 `json.JSONDecodeError`（`ValueError` 的子类），项目的 `parse_json_output()` 捕获它返回 None——**外部工具的输出永远当"不可信输入"处理**，这是与第三方进程打交道的铁律。

## 3.5 误报控制：`is_test_file` 与三处豁免

这一段讲项目最有说服力的一段工程叙事——"用自己审查自己"暴露的事故与修复。

**事故现场。** 开发到中段时，用 CodeSage 审查自己的 `tests/` 目录：**208 条候选发现里绝大多数是噪音**——CG006"缺少 docstring"报在每个测试函数上、Bandit B101（assert 使用）报在每条断言上。评分被扣到 0 分。这段记录完整保存在回归测试文件的注释里：

```python
"""误报控制测试：测试代码感知（dogfooding 反馈的回归保护）。

背景：用 CodeSage 审查自己的 tests/ 目录时，发现 208 条候选里
绝大多数是噪音（CG006 缺 docstring 报在测试函数上、Bandit B101
报 assert 用法），评分被扣到 0 分。本文件锁定修复后的行为：
"""
```

**修复：一个基础设施 + 三处豁免。** 基础设施是 `analyzers/base.py` 的 `is_test_file()`：

```python
def is_test_file(path: str) -> bool:
    """判断路径是否属于测试代码（误报控制的关键基础设施）。

    识别规则（pytest 生态的常见约定）：
    - 目录：路径中出现 test/ 或 tests/ 目录；
    - 文件名：test_*.py / *_test.py / conftest.py。

    为什么需要它：测试代码有其领域惯例——大量使用 assert、
    函数不写 docstring、fixture 中硬编码路径——这些在测试语境下
    是标准做法，报告它们属于典型误报（会淹没真实问题）。
    """
```

实现细节：先 `path.replace("\\", "/")` 归一化 Windows 路径，再按"文件名前缀/后缀 + 目录段精确匹配"判断。回归测试专门锁了两个**不能误伤**的反例：

```python
    assert not is_test_file("src/contest.py")   # 不误伤相近单词
    assert not is_test_file("latest_main.py")
```

`contest.py`、`latest_main.py` 名字里都有 "test" 字样——如果用子串匹配就误判了；实现用的是精确规则。

三处豁免各管一段：

| 位置 | 豁免内容 | 代码位置 |
|---|---|---|
| CG006 缺 docstring | 测试文件整体豁免（`if is_test_file(ctx.filename): return []`） | `ast_rules.py` |
| Bandit B101/B108 | `_TEST_FILE_SKIPS = {"B101", "B108"}`，且仅在测试文件中跳过 | `bandit_runner.py` |
| radon 复杂度 | 测试文件整体豁免（大量断言天然高 CC） | `radon_runner.py` |

**「豁免不是放水」**——回归测试里有一条专门守这条纪律：

```python
def test_bandit_still_reports_real_issues_in_test_files():
    """豁免仅限惯例用法：真实安全问题在测试文件中依然报告。"""
```

它构造一条 B602（`subprocess` + `shell=True`）的发现、文件名放 `tests/` 下——断言**照常报告**。豁免的只是"测试语境下合理的做法"；真实漏洞在哪都是漏洞。

## 3.6 小结与下一站

- **阶段代码**：一个 `RuleEngine` + 已安装的分析器列表；自研规则逐文件跑，外部分析器按 `supports_batch` 分流；产物 `det_findings` 是"候选发现"。
- **四零件**：`RuleContext`（工位，封装 Finding 构造与行号）、`Rule`/`NodeRule`（两种规则写法，模板方法 + 逃生口）、`RuleEngine`（单次解析多规则、语法错误报 CG000、规则异常隔离）。
- **13 条规则全表在手**；置信度分层是"候选 vs 结论"的语义；CG001/CG005/CG012 三条深读。
- **三个分析器**：统一 Analyzer 协议；`find_tool` 优先解释器目录（不激活 venv 也能跑）；Bandit 有"只升不降的高危上调表"、上限 MAJOR；批量模式是性能关键路径。
- **误报控制**：`is_test_file` + 三处豁免 + "豁免不放水"回归测试；全部来自一次 208 条噪音的真实事故。
- **Python 加餐**：类与 ABC、推导式全家桶、异常体系、**可变与不可变对象（★）**、subprocess 与 JSON。

下一站进入**阶段三 · 准备**：候选发现躺在 `det_findings` 里，接下来要把它们交给 Agent。整个过程从 `pipeline.py` 的 `# ----- 3. Agent 循环` 段落开始——第一件事是装配五个对象：LLM 客户端、模型路由、Token 估算器、预算、记忆。

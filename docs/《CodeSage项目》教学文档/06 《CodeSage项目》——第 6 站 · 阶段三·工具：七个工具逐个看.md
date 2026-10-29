# 《CodeSage项目》——第 6 站 · 阶段三·工具：七个工具逐个看

> 📍 **主线进度**：起点 ✅ → 扫描 ✅ → 确定性分析 ✅ → 组装 ✅ → 循环 ✅ → **▶ 工具（本篇）** → 后勤 → 合并与输出

循环在 3b 分支里执行 `self._registry.execute(tc.name, tc.arguments)` 时，模型真正"动手"了。这一篇把工具从协议到实现讲完整：注册表怎么把 Python 函数变成模型能调用的 JSON Schema（6.1），七个工具各自的签名、参数、真实输出与失败行为（6.2），以及它们合起来实现的"复核闭环"（6.3）。

## 6.1 工具注册表：从 `@tool` 到 `execute` 的完整链路

`agent/registry.py`。上一条链路是**注册**（函数 → schema），下一条链路是**执行**（模型参数 → 函数调用 → 文本结果）。

### 上半场：注册

第 04 篇已经看过 `@tool` 装饰器（只挂元数据）和 `Tool` 数据类（持有 name/description/parameters/func + `schema()` 方法）。现在看 `register()` 如何把两者缝起来：

```python
    def register(
        self,
        func: Callable[..., Any],
        name: str | None = None,
        description: str | None = None,
    ) -> Tool:
        """显式注册一个工具函数（可在 @tool 元数据之上覆盖名称/描述）。"""
        doc = inspect.getdoc(func) or ""
        summary, param_docs = _parse_docstring(doc)

        tool_name = name or getattr(func, "_tool_name", None) or func.__name__
        tool_desc = (
            description
            or getattr(func, "_tool_description", None)
            or summary
            or tool_name
        )

        properties: dict[str, Any] = {}
        required: list[str] = []
        signature = inspect.signature(func)
        for pname, param in signature.parameters.items():
            if pname in ("self", "cls"):
                continue
            schema = _annotation_to_schema(param.annotation)
            schema["description"] = param_docs.get(pname, pname)
            properties[pname] = schema
            if param.default is inspect.Parameter.empty:
                required.append(pname)

        parameters = {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }
```

四个决策层：

1. **名字与描述的三级回退**：显式参数 > `@tool` 挂的元数据 > docstring 摘要 > 函数名。给调用方（`build_default_tools`）留了覆盖口子；
2. **参数描述从 docstring 解析**：`_parse_docstring` 识别 `Args:` 段：

```python
def _parse_docstring(docstring: str | None) -> tuple[str, dict[str, str]]:
    """解析 docstring：返回 (摘要, {参数名: 描述})。

    支持的 Args 段格式：
        Args:
            path: 文件路径（相对工作区）
            start_line: 起始行号
    """
```

  解析规则：第一行是摘要；`Args:` / `Arguments:` / `参数:` 开始参数段；`snake_case: 描述` 逐行收集；遇到下一个段落标题（顶格的 `Xxx:`）结束。**"写好 docstring 就能得到好 schema"**——这是逼着工具作者把文档当接口写。
3. **类型注解 → JSON Schema**：`_annotation_to_schema` 做映射：

```python
_TYPE_MAP: dict[Any, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}

def _annotation_to_schema(annotation: Any) -> dict[str, Any]:
    """把 Python 类型注解转成 JSON Schema 片段。"""
    if annotation is inspect.Parameter.empty or annotation is Any:
        return {"type": "string"}
    # list[str] / list[int] 等泛型
    origin = getattr(annotation, "__origin__", None)
    if origin is list:
        args = getattr(annotation, "__args__", ())
        item_schema = _annotation_to_schema(args[0]) if args else {"type": "string"}
        return {"type": "array", "items": item_schema}
    if annotation in _TYPE_MAP:
        return {"type": _TYPE_MAP[annotation]}
    return {"type": "string"}
```

  策略很保守：四种基本类型直映射；`list[X]` 映射成 `array`（item 递归）；**其余一律回退 `string`**——认不出的注解宁可让模型传字符串，也不要生成看不懂的 schema。
4. **required 判定**：有默认值 → 不是必填；没有 → 进 `required` 列表。`additionalProperties: False` 明确告诉模型"不许带 schema 外的参数"（不过 `execute` 还会兜底过滤一次，见下）。

### 真实的 schema 长什么样（read_file 实测）

```json
{
 "type": "function",
 "function": {
  "name": "read_file",
  "description": "读取工作区内文件的内容（带行号，单次最多返回若干行）。",
  "parameters": {
   "type": "object",
   "properties": {
    "path": { "type": "string", "description": "相对工作区根目录的文件路径" },
    "start_line": { "type": "string", "description": "起始行号（1-based，默认从第 1 行开始）" },
    "end_line": { "type": "string", "description": "结束行号（0 表示自动读取 start_line 之后的若干行）" }
   },
   "required": ["path"],
   "additionalProperties": false
  }
 }
}
```

**注意一个实测出来的细节**：`start_line` 的注解明明是 `int`，schema 里却是 `"type": "string"`——这不是笔误。原因链：工具文件头部有 `from __future__ import annotations`，它让**所有注解在运行时变成字符串**（`"int"` 而不是类型对象 `int`）；`inspect.signature()` 默认不解析字符串注解；于是 `_TYPE_MAP` 查表落空，回退到 `string`。

实际影响不大（模型本来就倾向于按语义传数字，函数内部还有 `int(start_line)` 兜底转换），但这个"类型映射在真实工具上退化为 string"的细节值得如实记录——它也解释了为什么 `test_registry.py` 能测出 `"integer"`：那个测试模块没有加 future 导入，测试函数的注解是真类型对象，所以映射正常。**同一个机制，两种环境两种结果**——这类"测试通过但线上退化"的组合，正是 honest annotation 该抓的东西（第 12 篇诚实清单收录）。

> ### 【Python 加餐 21】f-string 进阶：格式说明符（Java 对照）
>
> 加餐 9 见了 f-string 的基础形态，现在把格式说明符（冒号后的那部分）讲全——项目输出层到处在用：
>
> ```python
> f"{i:>{width}} | {line}"     # 右对齐、总宽度 width（read_file 的行号列）
> f"{1_234_567:,}"             # 千分位 → "1,234,567"（报告里的 Token 数）
> f"{cost:.3f}"                # 定点 3 位小数 → "0.042"（成本 ¥）
> f"{ratio:.0%}"               # 百分比、无小数 → "62%"（上下文使用率）
> f"{score:>3}"                # 右对齐宽度 3
> f"{name:<8}"                 # 左对齐宽度 8（对齐表格）
> ```
>
> 结构是 `{变量:[[填充]对齐][宽度][,][.精度][类型]}`——和 Java 的 `String.format("%08.3f", x)` 同一套思想，但"就地写在变量旁边"，不用数位置参数。对照几个常用的：
>
> | 需求 | Python | Java |
> |---|---|---|
> | 千分位 | `f"{n:,}"` | `String.format("%,d", n)` |
> | 两位小数 | `f"{x:.2f}"` | `String.format("%.2f", x)` |
> | 百分比 | `f"{x:.1%}"` | `String.format("%.1f%%", x*100)` |
> | 右对齐补位 | `f"{x:>5}"` | `String.format("%5d", x)` |
>
> Python 的独门细节：**宽度可以是变量**（`f"{i:>{width}}"`——`>` 后嵌一个 `{width}`），Java 做不到这种动态宽度；下划线数字字面量（`1_000_000`）也常和它搭伙（本项目 `TokenUsage.cost_yuan` 里的 `1_000_000` 就是）。记住 `:` 前后分工：**冒号前是表达式，冒号后是格式**。

### 下半场：`execute()`——三道防线，永不抛异常

```python
    def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        max_result_chars: int = 6000,
    ) -> str:
        """执行工具：任何错误都转成可读文本返回（不向上抛出）。

        错误回填是刻意设计：模型看到错误信息后会调整策略（自我纠正），
        比让整个循环崩溃更健壮。
        """
        tool_obj = self._tools.get(name)
        if tool_obj is None:
            return f"ERROR: 未知工具 '{name}'。可用工具：{', '.join(self.names())}"

        # 过滤多余参数（模型偶尔会带上 schema 外的键）
        valid = set(tool_obj.parameters.get("properties", {}))
        clean_args = {k: v for k, v in (arguments or {}).items() if k in valid}

        try:
            result = tool_obj.func(**clean_args)
        except TypeError as exc:
            missing = tool_obj.parameters.get("required", [])
            return f"ERROR: 参数不匹配（{exc}）。required 参数：{missing}"
        except Exception as exc:  # noqa: BLE001 - 有意兜底，转文本回填
            return f"ERROR: 工具 '{name}' 执行失败：{exc.__class__.__name__}: {exc}"

        text = self._stringify(result)
        if len(text) > max_result_chars:
            text = text[:max_result_chars] + f"\n...(结果已截断，共 {len(text)} 字符)"
        return text
```

三道防线 + 一道截断，逐条过：

| 防线 | 防的场景 | 输出 |
|---|---|---|
| ① 未知工具 | 模型拼错工具名 | `ERROR: 未知工具 'xxx'。可用工具：…`（带清单，模型能改） |
| ② 参数过滤 | 模型带上 schema 外的键 | 静默剔除（`clean_args` 只留合法键），不报错 |
| ③ 异常转文本 | 参数缺失（TypeError）/ 工具内部任何异常 | `ERROR: 参数不匹配（…）。required 参数：[…]` / `ERROR: 工具 'xxx' 执行失败：类名: 消息` |

最后 `_stringify`（字符串直通、其他类型 `json.dumps`）+ 截断（超 6000 字符截断并标注"共 N 字符"）。

三个细节值得停一下：

- **`tool_obj.func(**clean_args)`**——`**` 把字典展开成关键字参数（`{"path": "a.py"}` → `func(path="a.py")`）。这就是执行链的最后一跳：模型给的 JSON 参数字典，最终变成一次 Python 函数调用；
- **`except TypeError` 单独处理**：TypeError 大概率意味着"参数签名对不上"，报错文案带上 `required` 列表——模型缺哪个补哪个；
- **`except Exception` 的 `# noqa: BLE001`**：这是"有意兜底"的显式声明，注释写明了理由——回填而非崩溃。

**这套设计有测试锁定**（`test_agent_loop.py` 的三个场景 + `test_registry.py` 的 schema/执行测试）：未知工具、缺参数、工具抛异常——循环都不崩，错误全部变成 `ERROR` 开头的 tool 消息。

## 6.2 七个工具逐个看

先看一份实测的"出厂清单"——注册表里的工具与顺序：

```text
['read_file', 'search_code', 'list_symbols', 'run_analyzer', 'query_knowledge', 'submit_finding', 'dismiss_candidate']
```

按信息获取（前三个）→ 零成本分析 → 知识检索 → 结论提交（最后两个）的顺序，逐个拆。

### 工具一：`read_file`——模型的眼睛

```python
    @tool(name="read_file")
    def read_file(path: str, start_line: int = 1, end_line: int = 0) -> str:
        """读取工作区内文件的内容（带行号，单次最多返回若干行）。

        Args:
            path: 相对工作区根目录的文件路径
            start_line: 起始行号（1-based，默认从第 1 行开始）
            end_line: 结束行号（0 表示自动读取 start_line 之后的若干行）
        """
```

| 参数 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `path` | str | 必填 | 相对工作区路径 |
| `start_line` | int | 1 | 起始行（1-based） |
| `end_line` | int | 0 | 结束行；0 = 自动读 start_line 后 `limit` 行（默认 500） |

**路径越权防护**——所有能碰文件的工具共用的第一关：

```python
def _resolve_inside(root: Path, path: str) -> Path | None:
    """把相对路径解析到工作区内；越权或非法路径返回 None（防路径穿越）。"""
    try:
        target = (root / path).resolve()
    except (OSError, ValueError):
        return None
    try:
        if not target.is_relative_to(root):
            return None
    except ValueError:
        return None
    return target
```

`(root / path).resolve()` 会把 `../../etc/passwd` 这类相对路径**解析成最终绝对路径**，然后 `is_relative_to(root)` 检查"还在工作区内吗"。模型再怎么胡说路径，也只能看到被审查的项目内部——这是"给模型的权限最小化"的具体实现。

**输出格式**（实测）：

```text
# order_processor.py（共 25 行，当前显示 1-12）
 1 | """订单处理（演示代码：故意包含逻辑 Bug 与异常处理缺陷）。"""
 2 | 
 3 | TAX_RATE = 1.13
 ...
12 |             amount = order["amount"] * TAX_RATE
...（还有 13 行未显示，可用 start_line=13 继续读取）
```

行号右对齐（宽度按最大行号算：`width = len(str(end))`），行号与内容用 ` | ` 分隔；结尾的续读提示是**写给模型的分页协议**——它知道怎么接着读。read_file 的上限 `limit` 由装配时 `read_limit=settings.review.tool_output_limit`（500）传入——**工具输出硬上限的第一道**。

错误文案全表（每种都带模型可行动的信息）：

| 情形 | 返回 |
|---|---|
| 路径越权/非法 | `ERROR: 非法路径（必须是工作区内的文件）：xxx` |
| 文件不存在 | `ERROR: 文件不存在：xxx` |
| 读取失败（OSError） | `ERROR: 读取失败：…` |
| 文件为空 | `ERROR: 文件为空：xxx` |
| 起始行超过总行数 | `ERROR: start_line=N 超过文件总行数（T）` |

### 工具二：`search_code`——零成本定位

```python
    @tool(name="search_code")
    def search_code(pattern: str, file_glob: str = "*.py") -> str:
        """在工作区源码中做正则搜索，返回"文件:行号: 内容"列表。

        Args:
            pattern: Python 正则表达式（如 "def get_" 或 "subprocess\\."）
            file_glob: 文件名过滤（默认 "*.py"）
        """
```

| 参数 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `pattern` | str | 必填 | Python 正则（`re.compile` 直接编译，非法则报错） |
| `file_glob` | str | `"*.py"` | `rglob` 的 glob 模式 |

行为要点：遍历 `root.rglob(file_glob)`（递归通配）；**跳过排除目录**（和扫描层同一张 `DEFAULT_EXCLUDES` 表——模型也翻不到 `.venv`）；每行匹配返回 `相对路径:行号: 内容[:160]`；结果上限 100 条，到顶时提示：

```python
        suffix = f"\n...（已达上限 {max_results} 条，请收窄正则）" if truncated else ""
```

实测输出：

```text
data_sync.py:44: except Exception:
data_sync.py:45: logger.exception("同步失败")
order_processor.py:14: except:
```

无匹配时返回 `（无匹配）`。**"请收窄正则"** 又是一句写给模型的行动指令。

### 工具三：`list_symbols`——低成本"目录"

```python
    @tool(name="list_symbols")
    def list_symbols(path: str) -> str:
        """列出文件的符号大纲（类 / 函数 / 方法签名 + 行号）。

        Args:
            path: 相对工作区路径
        """
```

用 `ast` 解析后只列**顶层**类/函数（类的方法缩进两格），参数只显示名字、有返回注解就带上（`ast.unparse` 还原）。实测输出（`src/codesage/analyzers/base.py`）：

```text
# src/codesage/analyzers/base.py 符号大纲（8 项）
L16 class Analyzer
  L24 def is_available(self) -> bool
  L28 def run(self, filename, source) -> list[Finding]
L32 def find_tool(executable) -> str | None
L51 def tool_exists(executable) -> bool
L61 def is_test_file(path: str) -> bool
L80 def run_command(cmd, stdin_text, timeout) -> subprocess.CompletedProcess[str] | None
L101 def parse_json_output(text) -> Any | None
```

**一个如实记录的行为细节**：`class Analyzer` 的基类 `ABC` 没有显示——实现里对基类有一句 `if not isinstance(b, ast.Name)` 的过滤，效果是**普通名称形式的基类（`(ABC)`、`(Rule)` 这类）不出现在大纲里**，只有更复杂的基类形式（如泛型下标）才会显示。对"了解文件结构"这个用途影响不大（模型照常能看到类的行号），但这个过滤的行为边界值得知道——测试目前没有覆盖基类显示这条路径。

适用与边界也都写在行为里：`ast.parse` 失败（语法错误）返回 `ERROR: 语法错误无法解析（line N）：…`；没有任何类/函数时返回 `（文件中没有类或函数定义）`。

### 工具四：`run_analyzer`——零成本的第二网

```python
    @tool(name="run_analyzer", description=f"对指定文件运行确定性分析器（零成本）。可用分析器：{available}")
    def run_analyzer(analyzer: str, path: str) -> str:
        """对指定文件运行确定性分析器。

        Args:
            analyzer: 分析器名称（ruff / bandit / radon）
            path: 相对工作区路径（.py 文件）
        """
```

| 参数 | 类型 | 含义 |
|---|---|---|
| `analyzer` | str | 分析器名（可用列表装配时动态生成；不在其中则 `ERROR`） |
| `path` | str | 目标文件 |

两个实现细节：

- **描述动态生成**：`description=f"…可用分析器：{available}"`——如果这次审查只装了 ruff，模型看到的描述里就只有 ruff。**给不了的能力，不让模型知道**（和第 04 篇"条件注册"同一个哲学，这里是"动态描述"版本）；
- **单次返回上限 30 条**（`MAX_ITEMS`），超出显示"...（另有 N 条未列出）"。

返回格式（构造示例）：

```text
bandit 在 order_processor.py 发现 3 个问题：
- [P1] order_processor.py:6 [B102] exec_used | …
- [P2] order_processor.py:14 [B110] try_except_pass | …
```

**这个工具的意义**：确定性分析不再只是开场的固定动作——模型可以随时说"对 admin_tools.py 再跑一遍 bandit"，而且零 Token（跑的是本地进程，只有结果文本进上下文）。

### 工具五：`query_knowledge`——权威依据

```python
    @tool(name="query_knowledge")
    def query_knowledge(query: str, top_k: int = 3) -> str:
        """检索最佳实践知识库（PEP / 惯用法 / 安全 / 性能 / 设计模式卡片）。

        当需要给出"最佳实践"类建议时，先检索知识库获得权威依据，
        避免凭印象给出不准确的建议。

        Args:
            query: 检索关键词（如 "EAFP 异常处理"、"SQL 注入"、"循环性能"）
            top_k: 返回卡片数上限（默认 3）
        """
```

`top_k` 会被夹在 1~5 之间（`top_k=max(1, min(int(top_k), 5))`）。返回的是卡片的标题 + tags + 正文（实测节选）：

```text
### Python 安全编码要点（OWASP 语境）（tags: 安全, 注入, SECURITY）
## Python 常见安全风险与修复

### 1. SQL 注入 —— 参数化查询（唯一正解）
...
```

**这个工具是"降低幻觉"的机制**：模型给"最佳实践"类建议前，先查权威卡片——建议有出处，不是凭印象。知识库本体在第 3.4 节提过（6 张卡片 + 关键词打分检索）。

### 工具六：`submit_finding`——结构化结论（复核闭环的上半场）

```python
    @tool(name="submit_finding")
    def submit_finding(
        file: str,
        line_start: int,
        line_end: int,
        category: str,
        severity: str,
        title: str,
        description: str = "",
        suggestion: str = "",
        confidence: float = 0.8,
    ) -> str:
```

| 参数 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `file` | str | 必填 | 相对路径（`lstrip("./")` 归一化） |
| `line_start` / `line_end` | int | 必填 | 1-based 行号（`end = max(start, end)` 防倒序） |
| `category` | str | 必填 | 五维度之一（非法值 → ERROR 带可用值） |
| `severity` | str | 必填 | critical/major/minor/info（同上） |
| `title` | str | 必填 | 一句话标题（截 200 字符；空 → ERROR） |
| `description` | str | `""` | 详细说明与触发条件 |
| `suggestion` | str | `""` | 修复建议（可含代码） |
| `confidence` | float | 0.8 | 置信度（夹在 0~1） |

**校验链**就是"错误回填、模型自纠"的示范：枚举非法 → 返回可用值列表；行号非整数 → 提示；行号 < 1 → 提示（1-based）；标题为空 → 提示。每一步的 ERROR 文案都是"喂给模型下一条指令"。

**同位置去重**（提交端的第一道去重）：

```python
        for idx, existing in enumerate(collector):
            if existing.dedup_key == finding.dedup_key:
                if finding.confidence > existing.confidence:
                    collector[idx] = finding
                    return f"已更新同位置发现（去重后共 {len(collector)} 条）：{finding.title}"
                return f"同位置已有更高置信度的发现，忽略本条（共 {len(collector)} 条）"
```

模型复核同一条候选超过一次时：高置信度覆盖低置信度，反之忽略。返回文案始终带"当前共 N 条"——**每次工具调用都让模型对系统状态保持感知**。

**本轮的修复点：换行归一化。** 模块里多了一个辅助函数（真实反馈驱动）：

```python
def _normalize_escaped_newlines(text: str) -> str:
    """还原模型偶尔输出的字面转义序列（\\n / \\r\\n）。

    背景：部分模型通过 function calling 提交多行文本时会双重转义——
    JSON 解析后得到的不是真实换行，而是字面 "\\n" 两个字符，
    终端与报告中会显示成一串 \\n，严重影响可读性。

    保守策略（避免误伤代码内容）：
    - 文本已含真实换行 → 不处理（无法区分代码里合法的 "\\n" 字面量）；
    - 字面 "\\n" 出现次数 < 2 → 不处理（单个通常是代码示例中的
      字符串字面量，如 print("a\\nb")）；
    - 其余情况视为双重转义错误，统一还原为真实换行。
    """
    if "\n" in text or "\r" in text:
        return text
    if text.count("\\n") < 2:
        return text
    return text.replace("\\r\\n", "\n").replace("\\n", "\n")
```

这是加餐 9 埋的伏笔兑现：`"\n"`（真换行）与 `"\\n"`（字面两字符）的区别在这里成了 bug 修复的主角。策略的三个分支全是"保守"二字——**修复数据问题时，先定义"什么情况下不动手"**。description 和 suggestion 两个字段走这个归一化（title 不走——单行标题没有换行问题）。`tests/test_tools.py` 锁定了这套行为（含"不误伤代码字面量"的反例）。

### 工具七：`dismiss_candidate`——复核闭环的下半场

```python
    @tool(name="dismiss_candidate")
    def dismiss_candidate(file: str, line_start: int, reason: str = "") -> str:
        """驳回一条候选发现（复核后判定为误报，不进最终报告）。

        复核候选发现时：判定为误报 → 调用本工具驳回；判定属实 →
        调用 submit_finding 重新提交（附修复建议）。

        Args:
            file: 候选发现所在文件（相对路径）
            line_start: 候选发现的行号（以候选清单中的行号为准）
            reason: 驳回理由（一句话说明为什么是误报）
        """
```

行为很薄：把 `{file, line, reason}` 记进 `dismissed` 列表，返回"已驳回候选：xxx（理由：…）。累计驳回 N 条"。

**为什么必须单独有这个工具**（模块注释说得最透）：

```python
"""dismiss_candidate 工具：驳回候选发现（LLM 复核结论的落地通道）。

为什么需要它：
- AST 规则的候选发现里必然有误报（如"未使用 with"但实际已手动 close）；
- 如果模型只是"不重新提交"，原规则发现仍会进入报告——复核形同虚设；
- 因此提供显式驳回通道：被驳回的候选在合并阶段被过滤掉，
  驳回记录写入报告 metadata（可观测、可审计）。
"""
```

**"不提交"≠"不存在"**——候选是规则引擎直接放进 `det_findings` 的，模型不显式驳回，它就会留在报告里。所以复核结论必须有一条独立的"删除通道"。

## 6.3 复核闭环：从候选到最终报告的完整链

把两个工具和管线接起来，闭环全貌：

```text
① 规则引擎产出候选（det_findings，含必然会有的误报）
        │  按严重级排序、截断 40 条，写进任务简报（第 04 篇）
        ▼
② 模型逐条复核（read_file 读周边代码；必要时 run_analyzer 复跑）
        │
        ├─ 属实 → submit_finding 重新提交（附修复建议，source=LLM）
        └─ 误报 → dismiss_candidate 显式驳回（记 file + line + reason）
                        │
                        ▼
③ 合并阶段（pipeline._merge_findings）：
   - 驳回过滤：被驳回的【确定性来源】发现从 det_findings 移除
     （容差 ±2 行；LLM 自己的发现不受驳回影响）
   - 去重：跨来源同位置保留优先级最高者（LLM > 自研规则 > Bandit > Ruff > radon）
        │
        ▼
④ 最终报告 = 过滤后的确定性发现 + LLM 发现（去重后）
```

驳回过滤的判定细节（第 08 篇还会完整重读这段）：

```python
        def is_dismissed(finding: Finding) -> bool:
            if finding.source.value == "llm":
                return False
            for d in dismissed:
                if d["file"] != finding.file:
                    continue
                if abs(d["line"] - finding.line_start) <= 2:
                    return True
            return False
```

- **±2 行容差**：模型的驳回引用的是候选清单里的行号，和规则原始 `line_start` 可能差一两行；
- **只滤确定性来源**：`source == "llm"` 直接豁免——驳回通道只作用于"规则/外部工具的候选"，不会误伤模型自己提交的发现。

还要注意守则 1 里写明的**去重优先级后果**："去重时你的版本优先于工具原始版本"——模型重新提交一条候选后，在合并去重阶段 `LLM` 来源压过 `AST_RULE` 来源，报告里展示的是**模型带上下文分析的版本**，而不是规则的通用提示。这就是"复核"为什么值得花 token 的回报机制。

## 6.4 【Python 加餐 22】装饰器（下）：项目里的三种形态对照

项目用到的装饰器刚好覆盖三种典型形态，摆在一起看：

| 装饰器 | 形态 | 做了什么 | 类比 |
|---|---|---|---|
| `@tool`（6.1 节） | **只挂元数据** | 给函数对象挂 `_tool_name` 等属性，原函数原样返回 | 最接近 Java 注解的"形似" |
| `@dataclass`（加餐 5） | **生成代码** | 读类注解，动态生成 `__init__`/`__repr__`/`__eq__` 塞进类 | 相当于运行时的 Lombok |
| `@property`（加餐 6） | **包裹行为** | 用描述符把"属性访问"劫持成函数调用 | 相当于给字段装了 getter |

三种形态的实现难度递增（挂属性 → 改类 → 实现协议），但对使用者的心智模型一致：**`@xxx` 的意思是"这个名字马上要被 `xxx(...)` 处理一遍"**。看到不认识的装饰器，先找它的定义——你总能找到一个"收进去一个东西、还回来一个东西"的普通函数（或类）。

再补一个项目里出现的"熟脸"——`@staticmethod`（`UnclosedOpenRule._inside_with` 等）：

```python
    @staticmethod
    def _inside_with(node: ast.AST, parents: dict[int, ast.AST]) -> bool:
```

静态方法：不需要 `self` 的方法。Java 程序员会问"那和顶层函数有啥区别"——区别只在"挂在类命名空间下"（`UnclosedOpenRule._inside_with(...)`），表达"这是这条规则的实现细节"。Python 的品味倾向其实更爱顶层下划线函数，但"逻辑属于这个类"时挂进去也自然。

## 6.5 小结与下一站

- **注册链**：`@tool` 挂元数据 → `register()` 反射生成 schema（名字三级回退、docstring 解析参数描述、类型映射、required 判定）；**实测细节**：future 导入让真实工具的 schema 类型退化为 string（已如实记录）。
- **执行链**：`execute()` 三道防线（未知工具 / 参数过滤 / 异常转文本）+ 输出截断——"错误是可回填的信息"的落点。
- **七个工具全表在手**：read_file（眼睛 + 越权防护）、search_code（正则定位）、list_symbols（结构大纲）、run_analyzer（零成本按需分析）、query_knowledge（权威依据）、submit_finding（结论 + 换行归一化修复）、dismiss_candidate（驳回通道）。
- **复核闭环**：候选 → 复核 → 提交/驳回 → 合并阶段过滤（±2 行、只滤确定性来源）→ 去重保优先级。
- **Python 加餐**：f-string 格式说明符（对齐/千分位/百分比/动态宽度）、装饰器三形态对照。

下一站是循环的"后勤系统"——**记忆、预算与 LLM 通信**。七个工具、每轮对话、每次压缩都在悄悄消耗同一种资源：上下文窗口。三区记忆怎么管理它、双阈值怎么守它、估算器怎么量它，以及压缩器本身的内部构造，全部展开在第 07 篇。

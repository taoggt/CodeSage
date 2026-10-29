# CodeSage 项目规划方案

> **定位**：面向个人开发者与团队的 AI 代码审查 Agent（CLI 形态）
> **目标**：Agent 开发实习生岗简历项目
> **一句话介绍**：`codesage review ./src` —— 一条命令完成代码质量分析、Bug 检测、性能建议、安全扫描与最佳实践推荐，输出分级评分报告。
> **核心约束**：零 Agent 框架依赖（自研内核）、单语言（Python）、一个月开发周期。

---

## 0. 项目定位

### 0.1 为什么这个项目适合写进简历

| 维度 | CodeSage 提供的证据 |
|---|---|
| Agent 原理理解 | 自研 ReAct 循环、工具注册协议、状态机、记忆压缩——每行代码都能讲清楚 |
| 工程落地能力 | CLI 产品化、配置管理、错误处理、SQLite 持久化、成本控制 |
| 评估思维 | 自研 Harness 评测框架：真实数据集、量化指标、A/B 回归 |
| 技术深度 | AST 规则引擎、静态工具链集成、双层记忆系统、自优化闭环 |

### 0.2 核心设计哲学（面试主论点）

市面上的"LLM 代码审查"玩具项目基本都是：读代码 → 塞进 Prompt → 打印模型输出。CodeSage 的核心判断是：

> **LLM 只在需要语义推理的环节介入，高确定性任务交给工程代码（零 Token）。**

三类任务的分工：

- **确定性任务**（文件筛选、AST 模式匹配、调用 Ruff/Bandit）→ 纯工程代码，零 Token
- **半确定性任务**（可疑片段的语义确认、误报复核）→ LLM 短上下文复核
- **开放任务**（逻辑错误推理、架构级建议、最佳实践推荐）→ LLM 深度审查

这一条对齐两个工业界已验证的经验：GitHub Copilot 团队公开的成本优化原则（"消除 harness 本可确定性完成的工作"）、阿里 Open Code Review 的设计原则（"确定性逻辑接管高确定性任务"）。**这是整个项目最有含量的论点，也是与套壳项目拉开差距的地方。**

### 0.3 不使用 Agent 框架的理由（面试必问）

不用 LangChain / LangGraph / CrewAI，而是自研内核，理由要说清楚：

1. **控制力**：CodeSage 的核心优化点是 token 预算与上下文压缩时机，框架的抽象层反而阻碍精确控制；
2. **可调试性**：自研 loop 每一步都有明确日志与 trace，出现幻觉/循环时能直接定位；
3. **零学习成本**：一个月周期内，学框架的抽象 → 遇到框架边界 → 绕开框架，反而不如直接写 loop 快；
4. **面试加分**：能讲清楚 function calling 协议、消息组装、循环终止条件、错误恢复，比"我用了 XX 框架"含金量高得多；
5. **依赖风险**：LangChain 生态 API 变动频繁，简历项目要的是稳定可展示。

---

## 一、项目整体结构与模块划分

### 1.1 目录树

```
codesage/
├── pyproject.toml              # 项目元数据 + 依赖（uv 管理）
├── README.md                   # 含架构图、评测数据、演示
├── .env.example                # API Key 模板
├── codesage.toml.example       # 项目配置文件示例
│
├── src/codesage/
│   ├── __init__.py
│   ├── cli/                    # ① CLI 表现层
│   │   ├── main.py             # Typer 入口，注册子命令
│   │   ├── commands/
│   │   │   ├── review.py       # codesage review <path>
│   │   │   ├── diff_review.py  # codesage review --diff（审查 git 变更）
│   │   │   ├── history.py      # codesage history（查看历史审查记录）
│   │   │   └── eval_cmd.py     # codesage eval（跑 Harness 评测）
│   │   ├── render.py           # Rich 报告渲染（表格/进度/高亮）
│   │   └── exporter.py         # markdown / json / sarif 导出
│   │
│   ├── core/                   # ② 领域核心（无外部依赖）
│   │   ├── models.py           # Finding / Severity / Category / ReviewReport
│   │   ├── config.py           # pydantic-settings 配置加载（toml + env）
│   │   └── errors.py           # 异常体系 + 统一错误处理
│   │
│   ├── ingest/                 # ③ 感知层：输入采集（零 Token）
│   │   ├── scanner.py          # 目录遍历 + .gitignore 规则 + 文件筛选
│   │   ├── chunker.py          # 大文件切块（按函数边界切，非滑动窗口）
│   │   ├── git_diff.py         # git diff 解析（取变更行号范围）
│   │   └── filters.py          # 预过滤：超大文件跳过、二进制跳过、diff 阈值
│   │
│   ├── analyzers/              # ④ 确定性分析层（零 Token）
│   │   ├── base.py             # Analyzer 协议：run(chunk) -> list[Finding]
│   │   ├── ast_rules.py        # 自研 AST 规则引擎（核心，~20 条规则）
│   │   ├── ruff_runner.py      # 集成 Ruff（代码风格/质量）
│   │   ├── bandit_runner.py    # 集成 Bandit（安全扫描）
│   │   └── radon_runner.py     # 集成 radon（圈复杂度/可维护性指标）
│   │
│   ├── agent/                  # ⑤ Agent 内核（自研，项目灵魂）
│   │   ├── loop.py             # ReAct 主循环：LLM ↔ 工具 交替执行
│   │   ├── registry.py         # 工具注册表：@tool 装饰器 + schema 自动生成
│   │   ├── state.py            # AgentState：dataclass + 序列化支持中断恢复
│   │   ├── context.py          # 上下文组装：system + 代码 + 工具结果 + 记忆
│   │   ├── policies.py         # 停止条件：轮次上限/预算耗尽/无新发现
│   │   └── profiles.py         # 角色 Profile：reviewer / security / perf
│   │
│   ├── tools/                  # ⑥ 工具集（Agent 可调用）
│   │   ├── read_file.py        # 读文件（带行数上限 500 行）
│   │   ├── list_symbols.py     # ast 提取函数/类签名（轻量索引）
│   │   ├── search_code.py      # 关键词/正则搜索（结果上限 100 条）
│   │   ├── run_analyzer.py     # 触发 Ruff/Bandit/radon 并结构化返回
│   │   ├── ast_query.py        # 针对片段的 AST 结构查询
│   │   └── query_knowledge.py  # 检索最佳实践卡片
│   │
│   ├── knowledge/              # ⑦ 知识库（轻量，非向量检索）
│   │   ├── corpus/             # 按类别组织的 markdown 知识卡片
│   │   │   ├── pythonic.md     # Python 惯用法
│   │   │   ├── security.md     # OWASP 常见问题（Python 语境）
│   │   │   ├── performance.md  # 性能反模式
│   │   │   └── patterns.md     # 设计模式建议
│   │   └── loader.py           # 卡片加载 + 关键词/标签检索
│   │
│   ├── llm/                    # ⑧ LLM 接入层
│   │   ├── client.py           # openai SDK 薄封装（OpenAI 兼容协议）
│   │   ├── router.py           # 模型分级路由（快模型/强模型）
│   │   ├── budget.py           # Token 预算：预估 + 阈值熔断
│   │   └── tokenizer.py        # TokenEstimator：启发式估算 / tiktoken 可插拔
│   │
│   ├── memory/                 # ⑨ 记忆系统（加分项）
│   │   ├── working.py          # 三区模型 + 双阈值压缩（会话内）
│   │   ├── project.py          # 项目级记忆（跨会话，SQLite）
│   │   └── store.py            # SQLite 封装（历史/发现/反馈）
│   │
│   ├── optim/                  # ⑩ 自优化（加分项）
│   │   ├── feedback.py         # 反馈采集（CLI 交互式标记误报/漏报）
│   │   ├── lessons.py          # 错题本：误报规则沉淀 + few-shot 示例池
│   │   └── prompts.py          # Prompt 版本管理 + 效果绑定
│   │
│   └── harness/                # ⑪ 评测框架（加分项，核心竞争力）
│       ├── dataset/            # 评测案例（YAML：代码 + ground truth）
│       ├── runner.py           # 批量执行 + 结果落盘（JSONL）
│       ├── metrics.py          # 检出率/误报率/成本/耗时/F1
│       └── compare.py          # A/B 对比 → 回归报告
│
└── tests/                      # pytest 单元测试
    ├── test_ast_rules.py
    ├── test_agent_loop.py      # Mock LLM 测循环逻辑
    └── test_memory.py          # 压缩触发逻辑测试
```

### 1.2 模块职责与调用关系

```
                    ┌─────────────────────────────────────┐
   用户输入 ────────► │  ① CLI 层 (Typer + Rich)            │
                    └──────────────┬──────────────────────┘
                                   │ 解析参数 / 加载配置
                    ┌──────────────▼──────────────────────┐
                    │  ③ 感知层 ingest                     │
                    │  扫描 → 筛选 → 切块 → 变更集解析      │
                    └──────────────┬──────────────────────┘
                                   │ list[CodeChunk]
              ┌────────────────────┼────────────────────┐
              │ 零 Token 快通道      │                    │
    ┌─────────▼─────────┐          │                    │
    │ ④ 确定性分析层      │          │                    │
    │ AST规则+Ruff+Bandit │          │                    │
    └─────────┬─────────┘          │                    │
              │ list[Finding]      │                    │
              └────────────────────┼────────────────────┘
                                   │ 候选问题 + 代码上下文
                    ┌──────────────▼──────────────────────┐
                    │  ⑤ Agent 内核（ReAct 循环）          │
                    │  ┌──────────────────────────────┐   │
                    │  │ LLM 决策 → 调用工具 → 观察 →   │   │
                    │  │ 再决策 ...（直到收敛/预算耗尽）│   │
                    │  └────────┬─────────┬───────────┘   │
                    │           │         │               │
                    │    ⑥ 工具集   ⑧ LLM 网关            │
                    │    (读码/搜索/  (路由+预算+熔断)      │
                    │     分析/知识)                       │
                    │           │         │               │
                    │    ⑨ 记忆系统 ◄──┘  ⑦ 知识库         │
                    │    (压缩/持久化)     (最佳实践卡片)    │
                    └──────────────┬──────────────────────┘
                                   │ 审查结果 + Critic 复核
                    ┌──────────────▼──────────────────────┐
                    │  报告聚合 → Rich 渲染 → 导出           │
                    │  (SQLite 落盘 → ⑩ 自优化反馈入口)      │
                    └─────────────────────────────────────┘

    ⑪ Harness 横向支撑：批量驱动①-⑤全链路，度量质量与成本
```

**调用原则**：

- `core/` 被所有模块依赖，自身零外部依赖（纯 dataclass + 异常定义）
- `analyzers/` 与 `agent/` 互不依赖，通过 `core.models.Finding` 解耦
- `agent/` 只通过 `tools/registry` 暴露能力，内核不认识具体工具
- `harness/` 只依赖 CLI 入口和 metrics，**不允许 import agent 内部模块**（保证评测走真实链路）

---

## 二、技术栈选型及理由

### 2.1 选型总表

| 层 | 选型 | 备选 | 选择理由 |
|---|---|---|---|
| Agent 内核 | **自研 ReAct 循环** | LangGraph / CrewAI | 零学习成本；精确控制 token 与压缩时机；面试能讲原理（详见 0.3） |
| LLM 接入 | **openai SDK**（OpenAI 兼容协议） | langchain-llm / litellm / 各家原生 SDK | 一个 SDK 通吃 DeepSeek/Qwen/GLM/OpenAI；原生 function calling 支持；无中间抽象层 |
| 模型 | **DeepSeek-chat（主力）+ DeepSeek-reasoner（深度分析）** | GPT-4o / Qwen | ￥1/百万 token 级成本，学生可承受；国内直连；reasoner 深度推理能力足 |
| CLI | **Typer + Rich** | argparse / Click | Typer 类型注解风格，2 天上手；Rich 表格/进度条/语法高亮开箱即用，演示效果直接拉满 |
| 配置 | **pydantic-settings + tomllib** | dynaconf / 纯 dataclass | pydantic 是面试高频考点，学会即赚；环境变量 + toml 双源，类型安全校验 |
| 静态分析 | **Ruff + Bandit + radon + 自研 AST 规则** | pylint / flake8 / semgrep | Ruff 是当前事实标准（极快、规则全）；Bandit 安全扫描成熟；自研 AST 规则引擎是项目深度所在；不选 semgrep（规则语言学习成本高，收益边际） |
| AST 解析 | **Python 标准库 ast** | tree-sitter | 单语言策略下标准库足够且零依赖（v2 扩展多语言时再引入 tree-sitter） |
| Token 计算 | **自研启发式估算器**（默认）+ tiktoken（可选） | 仅 tiktoken | 零依赖零联网（tiktoken 首次运行需下载编码文件，国内环境易卡住）；预算控制 ±20% 精度已足够（熔断阈值留有安全余量）；抽象 TokenEstimator 接口，需要高精度时一行切换 |
| 存储 | **SQLite（标准库 sqlite3）** | Postgres / 无存储 | 零部署；存历史审查/错题本/prompt 版本足够 |
| HTTP | **httpx** | requests | openai SDK 已依赖；异步能力备用 |
| 依赖管理 | **uv** | pip / poetry | 秒级安装/解析；一个命令建环境；当前社区趋势 |
| 测试 | **pytest + pytest-mock** | unittest | Mock LLM 响应测循环逻辑，行业标准 |
| 代码质量 | **Ruff (lint+format) + mypy** | black + isort + flake8 | 一个工具替代四个，配置量减 70% |

### 2.2 关键选型详解

**为什么自研 Agent 内核是合理决策而不是"重复造轮子"**

自研的边界要划清楚——只造三样东西，其余全部用现成方案：

1. **ReAct 主循环**（~150 行）：LLM 返回 tool_calls → 执行工具 → 结果回填 messages → 再调用，直到无 tool_calls 或触发停止条件。用原生 function calling 协议，不用 ReAct 文本解析（更可靠）。
2. **工具注册表**（~100 行）：`@tool` 装饰器从函数签名和 docstring 自动生成 JSON Schema，避免手写 schema 与实现脱节。
3. **记忆压缩器**（~200 行）：三区模型 + 双阈值触发，这是项目的核心差异化，必须自己写。

不造的部分：HTTP 重试（openai SDK 自带）、序列化（dataclass + json）、CLI 解析（Typer）。**判断标准：框架提供的能力如果是"标准且无优化空间"的（如 HTTP 重试），就用现成的；如果是"本项目核心竞争力"的（如记忆压缩、token 路由），必须自研并优化。**

**为什么砍掉 RAG / 向量库**

一个月周期内，embedding 选型 + 向量库部署 + 检索调优至少吃掉一周，且代码审查场景的检索需求并不强——最佳实践卡片总量可控（几十张），用「类别标签 + 关键词匹配」检索的效果与向量检索差距很小。v2 再演进为向量检索，演进路径本身就是面试可讲的取舍故事。

**为什么模型选 DeepSeek**

- 成本：约为 GPT-4o 的 1/20，Harness 批量评测（几十个案例 × 多轮调用）是**烧钱场景**，成本必须可控
- 兼容：提供 OpenAI 兼容接口，`openai` SDK + `base_url` 一行切换
- function calling：支持且稳定（这是自研 loop 的前提）
- 路由设计：快模型（deepseek-chat）跑工具决策与摘要压缩，强模型（deepseek-reasoner）只做最终深度审查——省钱的同时保证质量

---

## 三、核心功能的设计思路

### 3.1 代码质量分析（风格 / 命名 / 注释）

**分工设计**：

| 子能力 | 检测手段 | 是否耗 Token |
|---|---|---|
| 代码风格 | Ruff（E/W/F/I 规则集） | 否 |
| 命名规范 | Ruff（N 规则集）+ 自研 AST 规则（命名风格一致性） | 否 |
| 注释完整性 | 自研 AST 规则：公共函数缺 docstring、复杂函数（圈复杂度>10）缺注释说明 | 否 |
| 风格一致性（项目内） | 自研规则：同类文件间命名习惯冲突（如既有 `get_user` 又有 `fetch_user`） | 少量（LLM 确认语义） |

**自研 AST 规则示例**（`ast_rules.py` 核心设计）：

```python
class MissingDocstringRule(ast.NodeVisitor):
    """公共函数/类必须有 docstring。
    设计要点：
    - 白名单机制：__init__/__str__ 等魔术方法豁免（行业惯例，避免噪音）
    - 严重级分级：公共 API（无下划线前缀）为 WARNING，私有函数为 INFO
    - 修复建议生成：自动生成 docstring 模板
    """
```

规则引擎统一协议：`Rule.visit(node) -> list[Finding]`，通过 `ast.walk` 遍历单次解析多规则并行执行，避免重复解析。**每条规则必须带误报测试用例**（`test_ast_rules.py` 里正反例成对写）。

### 3.2 潜在 Bug 检测（逻辑错误 / 边界条件 / 资源泄漏）

这是**自研 AST 规则 + LLM 语义审查**配合最典型的场景。设计原则：AST 规则负责"发现可疑形态"，LLM 负责"确认是否真有问题"。

**AST 规则可覆盖的高频问题**（候选发现，零 Token）：

| 模式 | 检测方法 | 示例 |
|---|---|---|
| 可变默认参数 | `FunctionDef.defaults` 含 List/Dict/Set 字面量 | `def f(x=[])` |
| 裸 except | `ExceptHandler.type is None` | `except:` 吞掉所有异常 |
| 比较陷阱 | `is` 用于字面量比较；`==` 比较 None | `if x == None` |
| 资源泄漏 | `open()` 调用未在 `with` 内且未赋给变量/未 close | `f = open(p)` 后无 close 路径 |
| 边界条件 | 循环内 `range(len(x))` 与索引越界组合；`x[0]` 前无长度检查（结合控制流粗分析） | `arr[0]` 直接访问 |
| 可变对象泄漏 | 函数返回内部 list/dict 引用 | `return self._items`（无拷贝） |
| 整数除法陷阱 | `int / int` 期望整除但用 `/` | 价格计算 |
| 异常吞没 | except 块内只有 `pass` 或未 raise | `except: pass` |

**LLM 深度审查环节**：将「AST 候选发现 + 所在函数完整代码 + 调用方摘要」组装给 LLM，要求输出结构化 JSON（问题描述、触发条件、修复代码、置信度）。**关键设计**：LLM 输入不是整个文件而是「候选发现周边 ±50 行 + 函数签名索引」，这是 token 控制的主要手段。

**资源泄漏的动静态结合**：静态规则找出所有 `open()`/`socket`/`连接池` 调用点；对「未使用 with」的调用点，LLM 拿到该函数完整控制流（含 return/raise 路径）判断是否存在未关闭路径。这个"静态定位 + LLM 确认"的两段式设计，误报率显著低于纯 LLM 方案。

### 3.3 性能优化建议（瓶颈识别 + 方案）

**分层设计**：

1. **可量化指标层（零 Token）**：radon 计算圈复杂度、维护性指数；自研规则统计循环嵌套深度、函数长度。这些是客观数据，直接进报告。
2. **模式匹配层（零 Token）**：AST 匹配经典性能反模式——

| 反模式 | AST 检测 | 优化建议 |
|---|---|---|
| 循环内字符串拼接 | `For` 内 `AugAssign(+)` 字符串 | 改用 `list.append + "".join` |
| 循环内重复属性查找 | 循环体内高频 `obj.attr` | 提取到局部变量 |
| 列表成员检查 | `x in list_literal` 且列表较长 | 改 `set` |
| 不必要的列表物化 | `list()` 包裹后可迭代对象 | 直接用生成器 |
| 循环内重复计算 | 循环不变量（表达式在循环内但输入不变） | 提到循环外 |

3. **LLM 分析层（少量 Token）**：把「复杂度热点函数 + 调用链摘要 + 数据规模线索（如分页大小、批处理量）」给 LLM，产出"这个函数的性能风险点 + 具体优化方案（含代码）"。重点：**要求 LLM 给出复杂度量级判断（O(n²) → O(n)）**，避免"建议使用更高效的算法"这类空话——prompt 里明确禁止无具体方案的输出。

### 3.4 安全漏洞扫描（SQL 注入 / XSS 等）

**三层防御设计**：

```
第一层：Bandit 全量扫描（零 Token）
  └─ B608: SQL 拼接 / B703: Django SQL / B301: pickle / B105: 硬编码密码 ...
第二层：自研规则补充高置信度模式（零 Token）
  └─ f-string 直插 SQL（Bandit 会报，但自研规则补充"拼接变体"）
  └─ 未转义模板渲染：render_template_string(user_input)
  └─ subprocess shell=True + 变量拼接（命令注入）
  └─ yaml.load 未用 safe_load
第三层：LLM 污点分析（少量 Token）
  └─ 输入："用户输入来源点（request.args/input()）+ 危险汇聚点（execute/render）+ 中间数据流代码"
  └─ 让 LLM 判断：数据是否经过净化（参数化查询/转义函数）→ 是真漏洞还是误报
```

**Python 语境下的安全重点**：SQL 注入（sqlite3/MySQL 驱动字符串拼 SQL）、命令注入（subprocess/os.system）、反序列化（pickle/yaml.load/eval）、路径穿越（os.path.join 未净化）、SSRF、XSS（仅当检测到 Web 框架模板时）。

**误报控制的工程设计**：安全问题的误报代价高于漏报代价（开发者被误报骚扰后弃用工具）。因此设计**置信度分级**：Bandit HIGH + LLM 确认 = CRITICAL；仅 Bandit 报告且 LLM 未确认 = 降级为"待人工复核"。报告里明确标注哪些经过语义确认。

### 3.5 最佳实践推荐（语言特性 / 设计模式）

**知识卡片机制**（替代 RAG 的轻量方案）：

```markdown
---
id: py-eafp-vs-lbyl
tags: [异常处理, 惯用法, 边界]
trigger: [try-except 包住大段代码, KeyError 捕获]
python_version: ">=3.10"
---
## EAFP vs LBYL
Python 社区倾向 EAFP（先做再捕获异常）...
反例：先 if key in dict 再 dict[key]（存在竞态+冗余查找）
建议：直接 try: dict[key] except KeyError
```

**触发机制**：AST 规则/LLM 审查产出 Finding 后，`query_knowledge` 工具按 `tags/trigger` 匹配知识卡片，作为"权威依据"附在建议里。这个设计让最佳实践建议**有据可查**（引用 PEP8/PEP20/官方文档），而不是模型自由发挥——显著降低幻觉，也是与玩具项目的差距所在。

**设计模式建议的触发条件**（避免乱推荐模式）：只在 LLM 识别到明确信号时才给模式建议，如：
- 大量 `if-elif` 分支按类型分派 → 策略模式/字典分派
- 多个类共享相似构造流程 → 工厂模式
- 状态字段驱动大量条件分支 → 状态模式
- **反向约束**：prompt 明确"不要建议过度设计，单文件 50 行的项目不需要抽象工厂"

**类别全覆盖检查表**（写进 prompt，保证审查完整性）：`agent/profiles.py` 中 reviewer profile 的系统提示词包含固定的审查维度清单（质量/bug/性能/安全/最佳实践），要求输出前自检每个维度是否已覆盖，未覆盖需说明"本片段不涉及"。

---

## 四、面试加分项技术落地方案

### 4.1 Harness 评测框架（核心竞争力）

**设计目标**：让"CodeSage 效果怎么样"从主观感受变成可量化、可回归、可 A/B 的工程数据。面试时能拿出评测报告的项目，和只会说"效果不错"的项目，是两个层级。

**数据集构建**（成本最低、可信度最高的方案）：

从开源项目历史中挖真实 Bug：

```
筛选管道：
1. 选 3-5 个活跃 Python 开源项目（如 requests / flask 周边小库）
2. 爬取 git log，筛选 commit message 含 fix/bug/security 的提交
3. 每个提交：parent 版本 = buggy 代码（输入），diff + 测试变更 = ground truth
4. 人工过一遍，保留 25-30 个样本（覆盖 5 个功能类别，每类 5-6 个）
```

案例格式：

```yaml
# harness/dataset/case_012.yaml
id: case_012
source: { repo: "example/lib", commit: "a1b2c3d" }
category: resource_leak          # 对应五大功能类别
code_file: "snippets/case_012.py"  # buggy 版本
ground_truth:
  description: "文件句柄在异常路径下未关闭"
  line_range: [42, 58]
  severity: MAJOR
  keywords: ["open", "close", "with"]   # 用于匹配的软标准
```

**指标体系**：

| 指标 | 定义 | 为什么重要 |
|---|---|---|
| 检出率 (Recall) | ground truth 相关问题被检出的比例 | 核心能力 |
| 误报率 (FPR) | 报出问题中与 ground truth 无关的比例 | 决定工具是否被信任 |
| F1 | 上述两者调和平均 | 单一直观指标 |
| Token 成本/案例 | 端到端 token 消耗（分输入/输出统计） | 简历上写"单次审查成本 ￥0.0X"极其加分 |
| 耗时/案例 | 端到端 wall-clock 时间 | 体验指标 |
| 压缩触发次数 | 记忆压缩实际触发频率与节省 token 量 | 证明压缩机制的收益 |

**匹配逻辑**（`metrics.py` 的核心工程细节）：LLM 输出与 ground truth 的匹配不要求文本一致，采用「行号区间重叠 + 关键词命中」的软匹配：发现位置与 ground truth 行区间重叠 ≥30% 且关键词至少命中 1 个 → 判定为检出。**这个匹配器本身就需要写测试**（用构造的发现列表验证匹配/不匹配用例）。

**A/B 对比机制**（`compare.py`）：两次评测结果 diff → 生成回归报告（哪些案例从检出变漏报、误报变化、成本变化）。用途：
- prompt 修改前后对比
- 错题本注入前后对比（验证自优化有效性）
- 不同模型对比（deepseek-chat vs reasoner，成本 vs 质量曲线）
- 压缩阈值参数对比（60/80 vs 70/90 的权衡数据）

**运行方式**：`codesage eval --dataset harness/dataset --tag v0.3-baseline`，结果落 JSONL + 汇总卡片。支持 `--sample 5` 小样本快跑（开发期用）/ 全量跑（里程碑用）。

### 4.2 自动压缩记忆

**设计目标**：大文件/多轮审查不爆上下文；token 成本与审查规模呈**线性**而非指数增长。

**三区模型 + 双阈值触发**（对齐阿里 Open Code Review 已验证的工业设计）：

```
┌─────────────────────────────────────────────────┐
│  冻结区 (Frozen)                                 │
│  系统提示词 + 审查任务定义 + 知识卡片             │ ← 永不压缩
│  特点：跨轮次完全不变 → 可命中 prompt 缓存         │
├─────────────────────────────────────────────────┤
│  压缩区 (Compressed)                             │
│  中间轮次的工具调用结果、已有发现                 │ ← LLM 摘要压缩
│  压缩产物：结构化文本                             │
│  （发现的 file:line:问题，非自然语言总结）          │
├─────────────────────────────────────────────────┤
│  活跃区 (Active)                                 │
│  最近 K 轮对话原文（K=4）                         │ ← 保持原样
│  特点：保证推理连续性，模型能看到最近的完整上下文   │
└─────────────────────────────────────────────────┘

触发机制：
  使用率 < 60%   → 正常执行
  使用率 ≥ 60%   → 异步触发压缩（后台线程，不阻塞主循环）
  使用率 ≥ 80%   → 同步压缩（阻塞主循环，立即释放空间）
  使用率 ≥ 95%   → 熔断：强制结束当前审查，输出已有发现
```

**压缩器实现要点**：

1. **结构化压缩而非自然语言摘要**：压缩区内容让 LLM 输出 `{file, line, issue, evidence}` 列表，而非"我们讨论了..."的叙述——结构化产物无信息歧义，且后续检索用得上；
2. **压缩质量控制**：压缩 prompt 要求"保留所有已确认发现 + 所有未决疑点"，遗漏检查（压缩前后发现计数对比，不一致则重压一次）；
3. **工具输出硬上限**：`read_file` 500 行、`search_code` 100 条、`ast_query` 50 节点——从源头控制上下文增长（工程细节，面试可讲）；
4. **触发点埋在哪里**：在 `agent/loop.py` 每轮循环结束时调用 `memory.working.tick(usage_ratio)`，循环不感知压缩细节——**关注点分离**。

**项目级记忆**（`memory/project.py`，跨会话持久化到 SQLite）：

- 项目画像：主要语言、框架、目录结构摘要（首次审查时生成并缓存）
- 历史发现：`codesage history` 可查；重复审查同一文件时，已修复的问题不再重复报告
- 用户偏好：CLI 交互标记过的"不关心此类问题"→ 后续审查自动降级该类问题
- **设计约束**：项目级记忆注入冻结区（因为跨会话稳定），会话内记忆在压缩区

### 4.3 自优化机制

**设计目标**：让工具从"每次审查都从零开始"进化为"越用越准"，且所有优化都有**数据验证**（不是感觉变好了）。

**闭环设计**（三个环，按实现难度递进）：

**环 1：反馈采集 → 错题本**（必做）

```
用户侧：审查报告输出后，CLI 交互式提问
  "发现 #3 判断准确吗？ [y/n/s(跳过)]"
  "有没有漏报的问题？可选输入"
         │
         ▼
SQLite 落盘：{finding_id, verdict, category, snippet, context}
         │
         ▼
错题沉淀（optim/lessons.py）：
  - 误报样本 → 提取共性 → 生成排除规则（正则/AST 规则）进 rules_override
    例：误报集中在"测试代码中的 assert 与资源管理" → 添加测试目录豁免规则
  - 漏报样本 → 加入 few-shot 示例池 → 注入对应 profile 的 prompt
    例：漏报的 3 个案例都是"生成器提前 return 未触发 finally" → 作为示范加入
```

**环 2：Prompt 版本管理**（必做）

```
optim/prompts.py 维护 prompt 注册表：
{
  "reviewer": {
    "v1": { prompt_hash, created, diff_summary },
    "v2": { prompt_hash, ..., change: "加入错题本示例 3 条" }
  }
}
绑定机制：Harness 每次评测结果关联 prompt 版本
→ codesage eval --compare v1 v2 输出 A/B 报告
→ 回滚决策有据：v2 若在 5 个案例上从检出变漏报，回滚
```

**环 3：参数自适应**（选做，时间不够就砍）

基于历史数据自适应调整：压缩阈值（发现误报偏好高时提高阈值减少误报）、模型路由规则（某类问题快模型检出率达标则降级路由省钱）。**诚实标注**：环 3 是 v2 方向，一个月内不实现，面试时讲设计思路即可。

**防过拟合原则**（面试要点）：错题本示例池控制上限（每个 profile ≤5 条），且 Harness 数据集按「调优集/验证集」7:3 拆分——只在调优集上迭代，验证集数据变化是过拟合信号。**这个纪律感本身就是加分项。**

---

## 五、一个月开发计划

**假设投入**：每天 3-4 小时 × 每周 6 天 ≈ 21 小时/周，四周共 ~84 小时。
**原则**：每周结束有可演示产物；学习与开发 1:2 配比（边学边做）。

### 前置知识盘点 —— 为什么一个月够了

| 能力 | 要求 | 计算机专业大三的起点 | 补给时间 |
|---|---|---|---|
| Python 基础 | 类 / 装饰器 / 类型注解 / 异常 | 应已具备 | 0 |
| HTTP / JSON / git | 理解请求响应、版本控制 | 应已具备 | 0 |
| pytest | fixture / mock | 大概率学过，没学过也能半天补上 | 0.5 天 |
| Typer / Rich | 声明式 CLI + 终端渲染 | 没接触过 | 0.5 天 |
| **function calling 协议** | messages / tools / tool_calls 全链路 | 大概率没接触过 | **2 天（W1 核心）** |
| Python ast 模块 | NodeVisitor 遍历与规则编写 | 大概率没接触过 | 1 天（W2） |

**结论**：真正的新知识约 4 天，且已全部消化在 W1/W2 的开发任务里（不是脱离项目的空学）。一个月完全可行。**注意**：唯一需要"提前预习"的是 function calling ——它不在任何教科书里，只在 OpenAI 官方文档的 Function Calling 章节，建议 D3 那天先跑通官方文档的最小示例再动手。

### Week 1：工程基础 + 骨架 + LLM 入门（21h）

| 天 | 学习内容 | 交付物 |
|---|---|---|
| D1 | uv + pyproject.toml 工程结构；Typer 快速入门 | 项目骨架，`uv run codesage --help` 可用 |
| D2 | Rich（表格/进度条/Panel）；pydantic-settings 配置加载 | `codesage --version`、配置系统（toml+env）跑通 |
| D3 | **LLM API 核心**：messages 格式、function calling 协议、temperature 语义 | 手写脚本：让模型调用一个天气函数（理解 tool_calls 全流程） |
| D4 | token 估算器（策略模式：启发式估算 / tiktoken 可插拔）；流式响应与流式输出；错误处理与重试 | `llm/client.py` + `llm/budget.py` + `llm/tokenizer.py` 完成 |
| D5 | 第一个工具：read_file（含行数上限设计） | `tools/read_file.py` + `agent/registry.py` 雏形 |
| D6 | 复盘 + 补测试；写 W1 小结 | `codesage review <file> --dry-run` 能读文件并计数 token |

**W1 关键理解点**：function calling 的完整链路（请求带 tools schema → 响应含 tool_calls → 本地执行 → 结果以 role=tool 回填 → 再次请求）。**这是自研 Agent 的地基，必须吃透。**

### Week 2：Agent 内核 + 分析层（21h）

| 天 | 学习内容 | 交付物 |
|---|---|---|
| D1-2 | **ReAct 循环设计**：循环结构、消息组装、停止条件、异常恢复 | `agent/loop.py` 跑通：模型能自主调用 read_file 完成"找出文件中最长的函数" |
| D3 | AST 规则引擎设计：NodeVisitor 模式、规则的 visit 协议 | `analyzers/ast_rules.py` + 前 8 条规则（可变默认参数、裸 except、== None 等） |
| D4 | Ruff/Bandit/radon 集成：subprocess 调用 + JSON 输出解析 | `analyzers/*_runner.py`，统一 Finding 模型 |
| D5 | 规则补全至 ~15 条 + 每条规则的正反测试用例 | `test_ast_rules.py` 全部通过 |
| D6 | 端到端串联：感知层 → 分析层 → Agent 汇总 | `codesage review file.py` 输出结构化发现列表（JSON） |

**W2 关键理解点**：Agent 循环的**停止条件设计**（无 tool_calls / 达到轮次上限 / token 预算耗尽 / 连续 2 轮无新发现）；工具执行失败时如何回填错误信息让模型自我纠正（而不是直接崩溃）。

### Week 3：记忆压缩 + Critic + 报告（21h）

| 天 | 学习内容 | 交付物 |
|---|---|---|
| D1 | 三区记忆模型：分区数据结构 + 序列化 | `memory/working.py` 数据结构完成 |
| D2 | 压缩触发 + 压缩 prompt 设计 + 压缩质量校验 | 双阈值触发跑通，构造超长会话验证压缩生效 |
| D3 | Critic 节点：误报复核设计（二次 LLM 校验 + 置信度） | `profiles.py` 三角色 + Critic 链路 |
| D4 | 报告聚合与评分体系（0-100 分算法、分级 P0-P3） | `core/models.py` 的 Report 模型 |
| D5 | Rich 渲染：彩色分级表格、代码高亮、汇总面板 | 终端报告可演示（截图素材） |
| D6 | Markdown/JSON 导出 + SQLite 历史落盘 | `codesage history` 可用 |

**W3 关键理解点**：压缩的本质是**信息损失与成本的对偶**——压缩太早损失细节，太晚浪费 token。异步/同步双阈值的意义是"用并行性换响应延迟"。Critic 的价值用数据说话：W4 的 A/B 评测里对比"有/无 Critic"的误报率。

### Week 4：Harness + 自优化 + 收尾（21h）

| 天 | 学习内容 | 交付物 |
|---|---|---|
| D1 | 数据集挖掘：从开源仓库抓 bugfix commit（写个爬取脚本） | 25-30 个评测案例（YAML） |
| D2 | 指标实现：检出率/误报率/F1/成本统计 + 软匹配器 | `metrics.py` + 匹配器单测 |
| D3 | **跑第一次全量基线评测** + 记录数据 | 基线报告（简历里的核心数据） |
| D4 | 错题本 + prompt 版本 + A/B 对比跑通 | `codesage eval --compare` 有真实输出 |
| D5 | README：架构图 + 评测数据 + 快速开始 + 录屏 demo（asciinema） | 完整 README |
| D6 | 面试话术整理 + 代码 review 自查 + 打 tag v1.0 | 项目冻结，进入"可讲述"状态 |

**W4 关键理解点**：为什么"软匹配"而不是精确匹配（LLM 输出的自然语言无法精确比对）；为什么 A/B 要用同一数据集同一模型（控制变量）。

### 时间不够时的裁剪顺序（预案）

按优先级从低到高砍：
1. 参数自适应（环 3）→ 直接砍，只留设计文档
2. `diff_review` 子命令 → 砍（v2）
3. 知识卡片数量 → 每个类别 5 张保底
4. Critic 节点 → 降级为"Critic prompt 追加在 reviewer 同一次调用里"（低成本版）
5. 数据集规模 → 20 个案例保底

**绝不能砍的底线**：ReAct 内核、AST 规则引擎、记忆压缩、Harness 基线评测。这四样是简历的全部含金量所在。

---

## 六、关键决策取舍总表

| 决策点 | 选择 | 放弃的方案 | 核心理由 |
|---|---|---|---|
| Agent 框架 | 自研 ReAct 内核 | LangChain / LangGraph / CrewAI | 学习成本为零；token 与压缩需精确控制；面试讲原理 > 报框架名 |
| 语言支持 | 仅 Python | 多语言（tree-sitter） | 一个月内做深优先于做广；Python 语义分析可用标准库做透 |
| 多 Agent 架构 | 单内核 + 角色 Profile + Critic | 多独立 Agent 进程 | 代码复用率高，避免为"多"而"多"；Critic 保留复核叙事 |
| 知识检索 | 标签+关键词匹配卡片 | RAG / 向量库 | 卡片量小，检索收益边际低；省下一周工期；v2 演进有故事 |
| 模型策略 | DeepSeek 分级路由 | 全量 GPT-4o | 评测是烧钱场景，成本必须压低；兼容 OpenAI 协议无迁移成本 |
| 存储 | SQLite | 无持久化 / Postgres | 标准库零依赖，历史/错题本/版本管理刚好够用 |
| 报告形态 | CLI + Markdown 导出 | Web UI | 目标岗位是 Agent 开发，前端投入产出比低 |
| 评测匹配 | 软匹配（行区间+关键词） | 精确文本匹配 | LLM 输出自然语言不可精确比对，软匹配是唯一可行解 |

**被明确砍掉的功能及后续演进**（写进 README，是取舍能力的证明）：

```
v2 路线（不在一月范围内）：
  ├─ tree-sitter 多语言支持（架构已预留：analyzers/base.py 是语言无关协议）
  ├─ 向量检索知识库（knowledge/loader.py 换成 hybrid retriever 即可）
  ├─ MCP Server 化（把 tools/ 注册表暴露为 MCP 协议，供 Claude Code 等调用）
  ├─ GitHub Action / PR Bot 集成
  └─ 参数自适应（自优化环 3）
```

---

## 七、面试讲解准备（项目讲述框架）

面试官大概率会问的问题与对应话术锚点：

| 问题 | 回答锚点 |
|---|---|
| 为什么不用 LangChain？ | 0.3 节五条理由，重点讲"上下文压缩时机需要精确控制，框架抽象层阻碍优化" |
| Agent 循环怎么设计的？ | tool_calls 驱动而非文本解析；四个停止条件；工具失败回填让模型自纠 |
| 怎么控制成本？ | 三级分工（零 token 确定性层 / 快模型 / 强模型）+ 双阈值压缩 + 工具输出硬上限；给出真实数据"单案例 ￥X" |
| 怎么保证审查质量？ | Harness 基线数据 + 软匹配指标 + A/B 回归；误报率用有/无 Critic 的对比数据说话 |
| 自优化会不会过拟合？ | 调优集/验证集 7:3 拆分；示例池上限 5 条；所有优化必须过 A/B |
| 项目最大的难点？ | 推荐讲"压缩的信息损失权衡"：结构化压缩产物设计、压缩质量校验（发现计数对比）|
| 项目还能怎么改进？ | v2 路线：多语言、向量检索、MCP 化、参数自适应——展示有清晰的演进认知 |

---

*文档版本 v1.0 · 2026-10-11 · 基于「自研内核 + 一个月周期」约束制定*

# 《CodeSage项目》——收尾与附录：面试锚点、诚实清单与速查表

> 📍 **主线进度**：主线（01–08）与三条支线（09–11）全部完结。本篇是总索引：怎么讲（面试锚点）、哪里没做完（诚实清单）、怎么查（三张速查表）、以及 28 个 Python 加餐的一览。

## 12.1 面试讲述锚点

八个站 + 三条支线的核心论点，整理成"问题 → 锚点（含篇目定位）"：

| 面试官可能问 | 回答锚点 |
|---|---|
| 为什么不用 LangChain / LangGraph？ | ① 压缩时机需要精确控制——压缩发生在循环的哪两个时机是被设计过的（07 篇 7.2）；② 可调试——每一步都有事件流，Mock 就能覆盖循环的九个场景（11 篇 11.2）；③ 能讲原理而不是报框架名（function calling 协议闭环 05 篇 5.2、四个出口 05 篇 5.1、错误回填 05 篇 5.5） |
| Agent 循环怎么设计的？ | 一轮五步骨架（05 篇 5.1）；原生 function calling 而非文本解析（05 篇 5.1）；四个退出路径全部带文案进报告（05 篇 5.1）；三个停止条件 + 预算熔断（05 篇 5.4 / 07 篇 7.3）；十一项依赖显式注入（05 篇 5.3） |
| 怎么控制成本？ | 三级分工（01 篇 1.2）；候选限 40 条、工具输出截断 6000 字符、read_file 500 行（04 篇 / 06 篇）；三区压缩 + 三阈值（07 篇 7.2–7.3）；只有压缩走快模型（07 篇 7.5）；确定性层实测零成本（09 篇 9.6） |
| 怎么保证审查质量？ | 软匹配评测 + A/B 逐案例回归（09 篇）；复核闭环：候选→复核→提交/驳回→过滤（06 篇 6.3）；误报控制三件套：测试豁免 + "宁可漏报"守则 + 208 条噪音事故的修复（03 篇 3.5）；严重级按业务风险上调（11 篇 11.4 #3） |
| 自优化会不会过拟合？ | 三条纪律：经验池双上限（6 条/1200 字符）、优化必须过 A/B、调优/验证拆分（10 篇 10.6）；经验按项目隔离防串台（10 篇 10.3）；如实说明环 2 接线与数据集扩充是进行时（10 篇 10.5） |
| 项目最大的难点是什么？ | 推荐讲"压缩的信息损失权衡"：切点必须对齐 assistant 保住协议配对、保留清单白名单、失败宁可占 token 不丢信息、滚动合并语义（07 篇 7.2）；以及"停止策略的宽限期"——判定"没找到"之前先区分"还在找"（05 篇 5.4，来自真实掐断案例） |
| 遇到过什么真实问题？ | 11 篇 11.4 的十项台账任选：全局存储迁移（污染第三方项目）、SQL 注入与缺 docstring 同为 P2、双重转义 `\n`、`--changed` 静默全量、208 条噪音事故——每一项都有"反馈 → 修复 → 测试锚点"的完整链条 |
| 项目还能怎么改进？ | v2 路线（12.6）+ 三个已识别的接线项：PromptRegistry 接入主链路、chunk_source 接入、`--changed` 行区间级裁剪（12.2） |

**讲述节奏建议**：先用 01.3 的"五阶段全景"给出一张地图（30 秒），再选 05（循环）或 07（上下文经济学）深潜一个话题（3 分钟），最后用 11.4 的真实台账收尾（有事实、有数据、有反思）。**别背功能列表，讲"为什么"和"怎么发现的问题"。**

## 12.2 诚实清单：已落地 / 未接线 / 已预留

第 00 篇承诺过："对定义了但没有被使用的东西会明确标注。"汇总如下——**知道一个项目哪里没做完，和知道它哪里做完了同样重要**：

| 状态 | 项目 | 说明 |
|---|---|---|
| ✅ 完整落地 | 确定性层（13 规则 + 3 分析器）、ReAct 循环、三区记忆 + 三阈值、全局存储 + 历史下钻、评测体系、错题本闭环（含项目隔离）、CLI 四命令 | 均有测试覆盖与真实运行数据 |
| 🔌 已实现、未接线 | `PromptRegistry.register()`（`optim/prompts.py`） | 注册表与 `prompts` 表已就位、写入幂等；主链路无调用点——评测与 prompt 版本的自动绑定待接（10 篇 10.5） |
| 🔌 已实现、未接线 | `chunk_source()`（`ingest/chunker.py`） | 有测试覆盖；主链路暂未调用（大文件当前是"2000 行预过滤 + read_file 分段"）（02 篇 2.2） |
| 🔌 已实现、未接线 | `expand_ranges()`（`ingest/git_diff.py`） | 有测试；`--changed` 目前是文件级过滤，行区间级裁剪未接（02 篇 2.4） |
| ⏸ 定义未使用 | `TaskKind.DECISION` / `TaskKind.JUDGE` | DECISION 与 REVIEW 实际合并为同一次调用；JUDGE（LLM-as-judge）为评测增强预留（07 篇 7.5） |
| ⏸ 定义未使用 | `AgentState.snapshot()` / `TokenBudget.snapshot()` | 已实现、无调用点（序列化/调试扩展位） |
| ⏸ 定义未使用 | `LoopConfig.max_turns` 字段 | 被赋值但循环未读取（轮次上限实际走 `StopPolicy.max_turns`）；两处默认值一致故无实际影响（04 篇 4.6） |
| ⏸ 定义未使用 | `lessons.hits` 字段 | 表结构与读取就绪，无递增逻辑（"经验命中次数"预留） |
| ⏸ 定义未使用 | `WorkingMemory.events` 属性 | 压缩事件列表有记录、无外部读取方（观测扩展位） |
| ⏸ 无引用 | `scan_single_file()`（`bandit_runner.py`） | 注释标注"保留供独立调试使用"，全项目无调用点 |
| ⏸ 无引用 | `Store.review_count()` | 已实现、无调用点 |
| ⏸ 参数未用 | `submit_finding` 工具的 `workspace` 参数 | 签名保留、函数体内未使用 |
| 📄 文档与代码漂移 | `MockLLMClient` docstring 中的"`codesage review --mock` 演示模式" | 该 CLI 选项不存在；Mock 实际只用于单元测试（`eval --mock` 走 `use_llm=False`，不用这个类）（07 篇 7.6） |
| 🔍 行为细节 | 工具 schema 的类型映射 | 受 `from __future__ import annotations` 影响，真实工具的 schema 参数类型全部显示为 `string`（反射拿到字符串注解）；功能不受影响（函数内部有类型转换），测试未覆盖这一组合（06 篇 6.1） |
| 🔍 行为细节 | `list_symbols` 的基类显示 | 普通名称形式的基类（如 `(ABC)`、`(Rule)`）不出现在符号大纲中（实现有 `if not isinstance(b, ast.Name)` 过滤）（06 篇 6.2） |
| 🚧 已知功能边界 | `.gitignore` 完整语义 | 否定规则（`!`）、`**` 递归通配不支持（模块注释已坦白，02 篇 2.2） |
| ⏳ 待外部条件 | LLM 全链路评测数据 | 需配置 API Key 后运行 `codesage eval --tag v1-llm`（09 篇 9.6） |
| ⏳ 待数据积累 | 数据集扩充至 25-30 案例、调优/验证拆分 | 挖掘管道已写入 `examples/eval_dataset/README.md`（09 篇 9.3） |

## 12.3 速查表 A：命令与参数

```bash
# ---- review：审查（主线全流程）----
codesage review ./src                      # 完整审查（需 API Key）
codesage review app.py --no-llm            # 零成本模式（确定性层）
codesage review . --changed                # 增量：只审 git 变更文件（对比 HEAD）
codesage review . --profile security       # 角色：full / security / performance
codesage review . --format json -o out/    # 导出（-o 给目录自动命名）
codesage review . --min-severity P1        # 终端展示门槛（P0-P3 别名可用）
codesage review . --fail-on major          # CI：存在 P0/P1 则退出码 1
codesage review . --max-turns 6 -q         # 覆盖轮次上限 + 安静模式

# ---- eval：评测（支线一）----
codesage eval --mock --tag baseline        # 确定性层基线（零成本）
codesage eval --tag v1-llm                 # 完整链路（需 API Key）
codesage eval --sample 3 --tag quick       # 小样本快跑
codesage eval --compare baseline,v1-llm    # A/B 对比

# ---- feedback：反馈与错题本（支线二）----
codesage feedback 0f9a2657 -v wrong -n "该场景下可忽略"
codesage feedback 0f9a2657 -v missed -n "应报未报"
codesage feedback --mine                   # 提炼错题本
codesage feedback --show                   # 查看错题本（按项目）

# ---- history：历史与下钻（支线二）----
codesage history                           # 最近记录（全局，全部项目）
codesage history --detail 25               # 完整重渲染 #25 次审查
codesage history --project .               # 只看当前项目
codesage history --feedback                # 附反馈统计
```

`review` 参数速查：

| 参数 | 默认 | 说明 |
|---|---|---|
| `path`（位置） | `.` | 文件或目录 |
| `--profile, -p` | full | full / security / performance |
| `--format, -f` | terminal | terminal / markdown / json |
| `--output, -o` | 无 | 导出路径（目录自动命名 `<目标名>.codesage.<ext>`） |
| `--no-llm` | 关 | 只跑确定性分析 |
| `--changed` | 关 | 只审 git 变更文件（三态语义见 02 篇 2.4） |
| `--max-turns` | 配置值（12） | 覆盖循环轮次上限 |
| `--min-severity` | info | 展示门槛（critical/major/minor/info 或 P0–P3） |
| `--quiet, -q` | 关 | 不打印进度 |
| `--fail-on` | none | CI 退出码阈值（none/critical/major/minor 或 P0–P2） |

退出码：`0` 正常 / `1` 达到 `--fail-on` 阈值 / `2` 用法错误（参数非法、路径不存在、无文件可审、导出失败）。

## 12.4 速查表 B：配置项全表

优先级：**显式传入 > 环境变量 > `.env` > `codesage.toml` > 代码默认值**。环境变量规则：`CODESAGE_<组>__<字段>`（双下划线）。

| 组 | 字段 | 默认 | 环境变量 | TOML |
|---|---|---|---|---|
| llm | `api_key` | `""` | `CODESAGE_LLM__API_KEY` | —（建议放 .env） |
| llm | `base_url` | `https://api.deepseek.com` | `CODESAGE_LLM__BASE_URL` | `base_url` |
| llm | `model_strong` | `deepseek-chat` | `CODESAGE_LLM__MODEL_STRONG` | `model_strong` |
| llm | `model_fast` | `deepseek-chat` | `CODESAGE_LLM__MODEL_FAST` | `model_fast` |
| llm | `temperature` | `0.2` | `CODESAGE_LLM__TEMPERATURE` | `temperature` |
| llm | `max_output_tokens` | `4096` | `CODESAGE_LLM__MAX_OUTPUT_TOKENS` | `max_output_tokens` |
| llm | `timeout` | `120.0` | `CODESAGE_LLM__TIMEOUT` | `timeout` |
| budget | `context_window` | `64000` | `CODESAGE_BUDGET__CONTEXT_WINDOW` | `context_window` |
| budget | `soft_ratio` | `0.6` | `CODESAGE_BUDGET__SOFT_RATIO` | `soft_ratio` |
| budget | `hard_ratio` | `0.8` | `CODESAGE_BUDGET__HARD_RATIO` | `hard_ratio` |
| budget | `abort_ratio` | `0.95` | `CODESAGE_BUDGET__ABORT_RATIO` | `abort_ratio` |
| budget | `input_price_per_m` | `2.0` | `CODESAGE_BUDGET__INPUT_PRICE_PER_M` | `input_price_per_m` |
| budget | `output_price_per_m` | `8.0` | `CODESAGE_BUDGET__OUTPUT_PRICE_PER_M` | `output_price_per_m` |
| review | `max_turns` | `12` | `CODESAGE_REVIEW__MAX_TURNS` | `max_turns` |
| review | `active_window_turns` | `4` | `CODESAGE_REVIEW__ACTIVE_WINDOW_TURNS` | `active_window_turns` |
| review | `max_file_lines` | `2000` | `CODESAGE_REVIEW__MAX_FILE_LINES` | `max_file_lines` |
| review | `max_chunk_lines` | `400` | `CODESAGE_REVIEW__MAX_CHUNK_LINES` | `max_chunk_lines` |
| review | `tool_output_limit` | `500` | `CODESAGE_REVIEW__TOOL_OUTPUT_LIMIT` | `tool_output_limit` |
| review | `max_candidates_in_prompt` | `40` | `CODESAGE_REVIEW__MAX_CANDIDATES_IN_PROMPT` | `max_candidates_in_prompt` |
| review | `store_path` | `""`（→ `~/.codesage/store.db`） | `CODESAGE_REVIEW__STORE_PATH` | `store_path` |
| review | `ignore_dirs` | 12 项（见 01 篇 1.3） | `CODESAGE_REVIEW__IGNORE_DIRS` | —（列表，建议默认） |

API Key 兜底顺序（不设 `CODESAGE_` 前缀也认）：`DEEPSEEK_API_KEY` → `OPENAI_API_KEY` → `DASHSCOPE_API_KEY`。

## 12.5 【Python 加餐】总索引：28 个专题的 Java 对照

全部 28 个加餐按篇目排列（"★"为对 Java 背景最重要的五个）：

| # | 主题 | 出处 | 一句话 Java 对照 |
|---|---|---|---|
| 1 | 模块、包与程序入口 | 01 篇 | 目录即包（`__init__.py` 标记）+ `__main__`/entry point ≈ package + main class |
| 2 | 默认参数与关键字参数 | 01 篇 | 无重载 → 默认值 + 命名传参；**可变默认值 = 头号陷阱** |
| 3 | 类型注解与 pydantic | 01 篇 | 注解不强制；pydantic 在运行期补上校验 |
| 4 | Enum 枚举 | 01 篇 | `class Severity(str, Enum)` ≈ enum + 也能当字符串用 |
| 5 | dataclass | 01 篇 | ≈ Lombok `@Data` / record；`default_factory` 解决可变默认 |
| 6 | @property | 01 篇 | ≈ getter，但不需要括号；计算属性首选 |
| 7 | **pathlib 与 `with`** ★ | 02 篇 | `with` ≈ try-with-resources；`Path / "a"` 拼路径 ≈ `Path.resolve` |
| 8 | 生成器 | 02 篇 | ≈ 惰性 Iterator/Stream；`os.walk` 边遍历边产出 |
| 9 | f-string 基础 | 02 篇 | ≈ 模板字符串；`{}` 里可以是任意表达式 |
| 10 | 类、继承与 ABC | 03 篇 | ABC ≈ abstract class；无 private，下划线是约定 |
| 11 | **推导式全家桶** ★ | 03 篇 | ≈ Stream 的 map/filter/collect，四种形态一行完成 |
| 12 | 异常处理体系 | 03 篇 | 无 checked 异常；裸 except 是禁忌（CG002 抓它） |
| 13 | **可变与不可变对象** ★ | 03 篇 | 列表/字典共享引用；默认参数陷阱的完整原理 |
| 14 | subprocess 与 JSON | 03 篇 | ≈ ProcessBuilder；命令用列表传，天然防注入 |
| 15 | **装饰器（上）：@tool** ★ | 04 篇 | **不是注解！** 是运行时"函数替换"；注解是元数据 |
| 16 | 闭包与函数工厂 | 04 篇 | 闭包捕获变量本身（可改）；`make_xxx_tool` 模式 |
| 17 | 反射与鸭子类型 | 04 篇 | `inspect.signature` ≈ 反射取参数；无需接口也"像就行" |
| 18 | 真值判断与 None | 05 篇 | 空容器=假；None 用 `is` 比较（`== None` 是 CG004） |
| 19 | 多值返回与解包 | 05 篇 | 元组解包替代 Java 的出参/包装类；`enumerate` 给序号 |
| 20 | **作用域规则 LEGB** ★ | 05 篇 | 函数级作用域（非块级）；`nonlocal`/`global` 声明 |
| 21 | f-string 进阶 | 06 篇 | 格式说明符：千分位 `,`、对齐 `>`、百分比 `%`、动态宽度 |
| 22 | 装饰器（下）：三形态 | 06 篇 | 挂元数据 / 生成代码（dataclass）/ 包裹行为（property） |
| 23 | 切片与序列操作 | 07 篇 | `s[::-1]` 反转、负索引、切片总产生新对象 |
| 24 | Protocol 结构化类型 | 07 篇 | 与 Java interface 相反：不要求 implements，"像就行" |
| 25 | 异常链 `raise from` | 07 篇 | ≈ `new Error(msg, cause)`；对外统一、对内保留根因 |
| 26 | 排序与 lambda key | 08 篇 | `key=` 取排序键 ≈ `Comparator.comparing`；元组做多级排序 |
| 27 | sqlite3 与 json | 08 篇 | 标准库嵌入式 DB（无 JDBC 三件套）；`?` 参数化查询 |
| 28 | 三元表达式与格式化 | 08 篇 | `值1 if 条件 else 值2`；`str.join` 分隔符在前 |

## 12.6 关键数字与 v2 路线

**关键数字速查**：

| 维度 | 数字 |
|---|---|
| 源码规模 | 62 个 Python 文件，约 6637 行（`src/`，不含测试） |
| 测试 | 14 个文件 / 119 个用例 / 全量约 4.4s（1526 行） |
| 自研规则 | 13 条（CG001–CG013）+ CG000（语法错误）+ CG-LLM（模型提交标记） |
| 工具 | 7 个 |
| 知识卡片 | 6 张 |
| 评测种子案例 | 5 个（security×2 / bug / performance / quality） |
| 默认预算 | 上下文 64000 tokens；阈值 60% / 80% / 95% |
| 默认行为 | 轮次上限 12；活跃区 4 轮；候选注入 ≤40；工具输出截断 6000 字符；read_file ≤500 行 |
| 评分 | 100 − Σ（P0 15 / P1 8 / P2 3 / P3 1），下限 0 |
| 性能 | 自举审查 92.7s → 2.1s（约 44 倍；Bandit 90s+ → 1.5s） |
| 实测评测 | 确定性层 Recall 100%（5/5）、Precision 75%（近似）、F1 0.857、¥0/案例 |
| 靶场演示 | demo_target：6 文件 / 27 条发现（P1 10 & P2 13 & P3 4）/ 1.5s |

**v2 路线**（README 同款）：

- tree-sitter 多语言支持（analyzer 协议已语言无关，第 03 篇）
- 向量检索知识库（`knowledge.query()` 接口不变，内部换 hybrid retriever）
- MCP Server 化（把 `tools/` 注册表暴露为 MCP 协议，供外部 Agent 调用）
- GitHub Action / PR Bot 集成（`--fail-on` + `--format json` 已铺好路）
- 参数自适应（环 3：压缩阈值与模型路由的自动调优，第 10 篇）

**以及三个"顺手就能接上"的既有轮子**：PromptRegistry 接入主链路、chunk_source 接入、`--changed` 行区间级裁剪——基础件都在，差的是最后一段接线（12.2 清单）。

---

*本组文档基于 CodeSage v0.1.0 的当前源码整理（2026-10-13）；所有代码引用与源码逐字一致，数字均来自项目内的真实运行产物、README 与测试文件。*

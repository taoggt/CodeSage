# Java 评测数据集

与 `examples/eval_dataset`（Python）同构：每个 `case_*.yaml` 描述一个**已知有缺陷**的 Java 片段。

```bash
# 确定性层（零 Token，无需 API Key）
codesage eval --dataset examples/eval_dataset_java --mock --tag java-baseline
```

当前基线（`--mock`）：**8 案例 / 检出 8 / Recall 100% / Precision 100% / F1 1.000**

## ⚠️ 这个 100% 能说明什么、不能说明什么

**能说明**：9 条自研 Java 规则从"语法树检测"到"报告落盘"整条链路是通的——
文件准入、语言分派、行号换算（tree-sitter 是 0-based）、严重级映射、
软匹配口径都没有错位。这类"接线错误"才是新语言接入时最常见的失败模式。

**不能说明**：工具能发现真实项目里的 Java 缺陷。每个片段都是**照着规则的
触发特征构造**的（见下表对应关系），100% 是设计出来的，不是测出来的。
真正的召回率要靠真实 bug 挖掘管道（同 Python 数据集 README 的 Week 4 计划）。

所以这里的定位是**回归网**：改规则、改解析器、改分派逻辑时，这 8 个案例
必须仍然是 8 个检出；一旦掉到 7 个，说明链路断了，而不是"模型不行"。

## 案例与规则对应

| 案例 | 类别 | 缺陷 | 对应规则 |
|---|---|---|---|
| case_001 | bug | 空 catch 块 | JG001 |
| case_002 | bug | `catch (Throwable)` 过宽捕获 | JG002 |
| case_003 | security | `Runtime.exec` 接收拼接参数 | JG003 |
| case_004 | security | 硬编码 API Key | JG004 |
| case_005 | security | SQL 字符串拼接注入 | JG005 |
| case_006 | bug | `FileInputStream` 未用 try-with-resources | JG006 |
| case_007 | bug | static 的 `SimpleDateFormat` | JG008 |
| case_008 | bug | 重写 `equals` 未重写 `hashCode` | JG009 |

JG007（循环内字符串 `+=`）与 JGCC（圈复杂度）没有单独案例：前者需要一个
能完整解析的循环上下文，后者需要 10+ 分支的方法，都会让"缺陷行"变得不明确，
用 `examples/java_demo/ReportBuilder.java` 覆盖即可。

## 字段说明

| 字段 | 说明 |
|---|---|
| `id` | 案例编号（与 `--compare` 输出对齐） |
| `category` | security / bug / performance / quality |
| `snippet` | 片段路径（后缀必须在语言注册表里，否则加载即报错） |
| `ground_truth.line_range` | 缺陷行区间（1-based，含两端） |
| `ground_truth.keywords` | 软匹配关键词，命中 finding 的 title/description/suggestion 任一即可 |

## 扩充方式

1. 从活跃的 Java 开源项目（Spring 生态、Apache 组件）筛 `fix` / `security`
   相关 commit；
2. 取 parent 版本的片段作为 buggy 代码；
3. 从 diff 推导 `line_range`，从 commit message 提炼 `keywords`；
4. 人工复核后加入本目录。

注意：跨文件的问题（如 `@Transactional` 自调用、跨方法拼接 SQL）当前无法被
确定性层覆盖，若要纳入评测，只能作为 LLM 模式的案例并用 `--tag` 区分基线。

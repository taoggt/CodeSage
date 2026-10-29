# 《CodeSage项目》——支线一：评测体系

> 📍 **主线进度**：主线（01–08）已完结；本篇是**支线一**。评测不是一次审查的环节，而是"反复驱动本来那条主线"的横向设施。

主线讲完了"一次审查怎么发生"，接下来回答更狠的问题：**它干得好不好？** "效果不错"是句没有信息量的话——"5 个案例检出率 100%、精确率 75%、F1 0.857、单案例成本 ¥0"才是可以讨论、可以回归、可以对比的工程事实。`harness/` 模块把前者变成后者。

## 9.1 `codesage eval` 命令：参数与两种模式

```python
def eval_command(
    dataset: Path = typer.Option(
        Path("examples/eval_dataset"), "--dataset", "-d", help="评测数据集目录"
    ),
    tag: str = typer.Option("baseline", "--tag", "-t", help="本次评测标签"),
    mock: bool = typer.Option(
        False, "--mock", help="mock 模式：只跑确定性层（无需 API Key，适合 CI）"
    ),
    sample: Optional[int] = typer.Option(
        None, "--sample", "-n", help="只跑前 N 个案例（开发期快速迭代）"
    ),
    compare_tags: Optional[str] = typer.Option(
        None, "--compare", help="A/B 对比模式：传两次评测标签（tagA,tagB），返回对比报告"
    ),
    results_dir: Path = typer.Option(
        Path(".codesage-eval"), "--results-dir", help="评测结果存放目录"
    ),
) -> None:
    """运行 Harness 评测：检出率 / 误报率 / 成本 / A-B 对比。

    示例:
      codesage eval --mock --tag baseline       # 确定性层基线（零成本，无需 Key）
      codesage eval --tag v1-llm                # 完整链路评测（需 API Key）
      codesage eval --sample 3 --tag quick      # 小样本快速迭代
      codesage eval --compare baseline,v1-llm   # A/B 对比两次评测
    """
```

| 参数 | 默认 | 含义 |
|---|---|---|
| `--dataset / -d` | `examples/eval_dataset` | 数据集目录 |
| `--tag / -t` | `baseline` | 本次评测的标签（结果文件名 = 标签） |
| `--mock` | False | 只跑确定性层（**真实跑**确定性分析，不是假数据） |
| `--sample / -n` | 无 | 只跑前 N 个案例（开发期快跑） |
| `--compare` | 无 | 对比模式：`tagA,tagB` |
| `--results-dir` | `.codesage-eval` | 结果目录 |

**两种模式**分流得很干净：

- **评测模式**（默认路径）：`--compare` 没给就走这里。先查配置——非 mock 模式且没有 API Key 时直接提示并退出（"可用 --mock 跑确定性层评测，或配置 .env 后运行完整评测"）。之后构造 `EvalRunner` 跑批量，最后渲染 summary 表：

```text
┌──────────────── 评测结果：baseline-final ────────────────┐
│ 指标                     值                              │
│ 案例数                   5                               │
│ 检出数                   5                               │
│ 检出率 Recall            100.0%                          │
│ 精确率 Precision（近似） 75.0%                            │
│ F1                       0.857                           │
│ Token 总量               0                               │
│ 总成本                   ¥0.0000                         │
│ 单案例平均成本           ¥0.0000                         │
│ 总耗时                   11.6s                           │
└──────────────────────────────────────────────────────────┘
```

- **对比模式**：`--compare tagA,tagB` 解析出两个标签（不是两个就报错退出），调用 `compare()` 渲染 A/B 表（9.5 节）。

## 9.2 `EvalRunner`：批量执行与隔离工作区

运行器的模块注释交代三条关键设计：

```python
"""评测运行器：对数据集批量执行真实审查链路并落盘结果。

关键设计：
- 走真实链路（调用 pipeline.run_review），而不是直接调分析器——
  保证评测结果反映用户实际使用时的行为（harness 不 import agent 内部模块）；
- mock 模式（--no-llm）只跑确定性层：无 API Key 也能验证指标管线、
  为 CI 提供稳定的回归基线；
- 结果落盘 JSONL（逐案例）+ summary JSON（聚合指标），
  供 compare 做 A/B 回归。
"""
```

**第一条最重——"走真实链路"**。实现手法是**函数内延迟导入**：

```python
    def run(self, tag: str) -> dict:
        """执行评测，返回 summary，并写盘。"""
        from codesage.pipeline import ReviewOptions, run_review
```

为什么不直接调分析器？因为那样测的是"分析器的能力"，不是"用户敲命令时系统真实的行为"（扫描→分析→Agent→合并→报告完整跑）。为什么延迟导入？让"harness 不依赖 agent 内部模块"这条架构纪律在 import 层面也成立——第 01 篇 1.3 节讲过这个细节。

每个案例的执行由 `_run_single_case` 负责，核心是**隔离工作区**：

```python
        with tempfile.TemporaryDirectory(prefix="codesage_eval_") as tmp:
            tmp_path = Path(tmp)
            # 隔离工作区：只放该案例的片段（保证报告的问题都针对目标代码）
            shutil.copy2(case.snippet_path, tmp_path / case.snippet_name)

            report = run_review_fn(
                tmp_path,
                self._settings,
                options_cls(
                    use_llm=not self._options.mock,
                    profile=self._options.profile,
                    quiet=True,
                ),
            )
```

三件事：为案例开一个**临时目录**（`with` 上下文管理器——退出自动清理，加餐 7 的第二种用法）；把案例片段**单独复制**进去（`shutil.copy2` 保留元数据）；在这个干净目录里跑完整审查。**"一个案例 = 一个干净的世界"**——案例之间的文件互不干扰，测量才成立。跑完从报告里取出数据：

```python
        result = evaluate_case(report.findings, case)
        result.update(
            {
                "tokens": report.usage.total,
                "cost_yuan": report.metadata.get("cost_yuan", 0.0),
                "duration_sec": round(report.duration_sec, 2),
                "turn": report.turns,
                "stop_reason": report.stop_reason,
                "reported_titles": [f.title for f in report.sorted_findings()][:5],
            }
        )
```

每个案例的最终记录兼顾"质量指标"（detected/matched/reported）和"成本指标"（tokens/cost/duration/turn）——外加**过程快照**（stop_reason 和前 5 条发现标题，调试时能一眼看出"这次为什么没检出"）。

落盘分两个文件（文件名都带 tag）：

```python
        jsonl_path = self._results_dir / f"{tag}.jsonl"           # 逐案例一行一条 JSON
        summary_path = self._results_dir / f"{tag}_summary.json"  # 聚合指标
```

**JSONL**（JSON Lines：一行一条记录的格式）适合追加与逐行消费；**summary** 是 `aggregate()` 的结果 + 运行元数据（tag/mock/profile/耗时/总 token/总成本/逐案例明细）。项目目录里的 `.codesage-eval/` 就沉淀着多轮真实结果：`det-baseline`（最初基线）、`det-after-noise-fix`（误报修复后）、`baseline-final`、`final-check`——每个标签都是一次可回溯的实验存档。

## 9.3 数据集：案例格式与扩充管道

一个案例一个 YAML 文件，加载成 `EvalCase`：

| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | str | 案例编号 |
| `category` | str | 维度：security / bug / performance / quality |
| `description` | str | 案例描述 |
| `snippet_path` | Path | 缺陷代码片段的绝对路径 |
| `line_start` / `line_end` | int | ground truth 的行区间 |
| `keywords` | list[str] | 软匹配关键词 |
| `severity` | str | ground truth 严重级 |
| `dataset_dir` | Path | 所属数据集目录 |

拿 case_001 说话——YAML 全文：

```yaml
id: case_001
category: security
description: SQL 注入 - f-string 拼接用户输入进查询语句
snippet: snippets/case_001.py
ground_truth:
  line_range: [9, 10]
  severity: major
  keywords: ["sql", "execute", "注入"]
```

对应片段：

```python
"""用户服务模块（评测案例：SQL 注入）。"""
import sqlite3


def get_user(username: str):
    """按用户名查询用户记录。"""
    conn = sqlite3.connect("app.db")
    cursor = conn.cursor()
    query = f"SELECT id, name FROM users WHERE name = '{username}'"
    cursor.execute(query)
    return cursor.fetchone()
```

注意 **ground truth 的构成 = 行区间 + 严重级 + 关键词**——没有任何"标准答案文本"。这正是 9.4 软匹配的前提。

种子集 5 个案例的覆盖面：

| 案例 | 维度 | 缺陷 |
|---|---|---|
| case_001 | security | SQL 注入（f-string 拼接） |
| case_002 | bug | 资源泄漏（open 未 with）+ 可变默认参数 |
| case_003 | performance | 循环内 `+=` 拼接字符串（O(n²)） |
| case_004 | quality | 裸 except + `== None` + 公共函数缺 docstring |
| case_005 | security | 命令注入（`subprocess shell=True`） |

**诚实标注**：5 个手工构造的种子案例离"统计显著"还很远（best_practice 维度暂无案例）。扩充管道写在 `examples/eval_dataset/README.md`（从开源项目挖真实 bugfix commit）：

```text
1. 选择 3-5 个活跃 Python 开源项目；
2. 筛选 commit message 含 fix / bug / security 的提交；
3. 取 parent 版本的相关文件片段 = buggy 代码（snippet）；
4. diff 与测试变更 → 推导 ground_truth 的 line_range 与 keywords；
5. 人工复核后放入本目录，目标 25-30 个案例（5 类 × 5-6 个）。
```

**为什么要用真实 bug 而不是自造**：bugfix commit 天然带着"修改前的错误代码"和「修复即验证」的隐式 ground truth，可信度和说服力都高于"出题人自己写错自己知道"。加载器（`dataset.py`）对数据质量做硬校验：缺 `snippet` 字段报错、片段文件不存在报错、`yaml.safe_load` 失败报错——**脏数据集比没有数据集更糟**，宁可拒绝加载。

## 9.4 软匹配：为什么不能精确比对

评测里最容易设计错的一环。模块注释把困难和方案都写清楚了：

```python
"""指标：软匹配 + 检出率 / 误报率 / F1 聚合。

为什么需要"软匹配"（核心工程细节）：
- ground truth 是"第 8-9 行有 SQL 注入"；
- 模型输出是自然语言发现（"execute 使用 f-string 拼接"）；
- 精确文本比对不可行，采用三重软匹配：
    1) 文件名匹配（basename）
    2) 行区间重叠 ≥ 30%
    3) ground truth 关键词至少命中 1 个
  三个条件同时满足才判定"检出"。

匹配器的局限（诚实标注）：
- 同一文件中的其他真实问题会被计入误报（工作区按案例隔离可缓解）；
- 关键词需要人工选择（一般取问题特征词，如 "execute"、"shell"）。
"""
```

判定实现——三个条件全部满足才算命中：

```python
def match_finding(finding: Finding, case: EvalCase, min_overlap: float = 0.3) -> bool:
    """判断一条发现是否命中 ground truth。"""
    if not _file_match(finding.file, case):
        return False

    if overlap_ratio(
        (finding.line_start, finding.line_end),
        (case.line_start, case.line_end),
    ) < min_overlap:
        return False

    if case.keywords:
        haystack = " ".join(
            [finding.title, finding.description, finding.suggestion]
        ).lower()
        if not any(k.lower() in haystack for k in case.keywords):
            return False
    return True
```

三段的形式化：

**① 文件名匹配**：`finding.file` 归一化（反斜杠转正斜杠、去掉 `./`）后以 `case.snippet_name` 结尾。

**② 行区间重叠 ≥ 30%**：

```python
def overlap_ratio(a: tuple[int, int], b: tuple[int, int]) -> float:
    """区间重叠比例 = 交集长度 / 较短区间长度。"""
```

**分母选"较短区间"**是一个对检出方友好的选择：规则常常只报问题行（区间窄），ground truth 可能覆盖两三行——用较短者做分母，避免"报得准反而被罚"。

**③ 关键词命中**：`title`/`description`/`suggestion` 三个字段拼成 haystack，**任一关键词命中**即可（`any(...)` + 生成器表达式）。关键词取"问题特征词"（`execute`、`shell`、`注入`）——宁可宽一点，因为还有 ② 的区间锁兜底。

**局限的坦白**（注释里列的两条，值得展开）：

- **"同一文件中的其他真实问题会被计入误报"**：case_004 的片段里同时有裸 except、`== None`、缺 docstring 三个问题，系统把它们都报出来——但 ground truth 只登记了"区间内至少抓到一个"，多报的进不了 matched，就落在精确率的分母上。所以项目的 **precision 是近似指标**，README 也如实标注（"精确率为近似指标：案例工作区隔离后，同一片段中的其他真实问题也会计入分母"）。缓解手段就是隔离工作区（减少无关文件），但同一片段内的多问题无解——**接受它，并把它写清楚**；
- **关键词人工选择**：软匹配的固有成本。换来的是"模型措辞变化不再影响评测"——用 `"execute"` 而非"必须出现『SQL 注入』四个字"。

顺带一件事：**这个匹配器自己也有一整个测试文件**（`test_metrics.py`，8 个用例，手工构造 `EvalCase` 与 `Finding` 覆盖"文件名不符/区间不重叠/关键词不命中/全部满足"）。评价系统的正确性必须独立可见——否则"检出率"这个数字本身就不可信。

## 9.5 指标与 A/B 对比

### 聚合口径

```python
def aggregate(results: list[dict]) -> dict:
    """聚合指标。

    - recall（检出率）= 检出案例数 / 总案例数
    - precision 近似 = 全部命中发现数 / 全部报告发现数
      （每案例工作区隔离，报告的问题都针对目标片段；
        未命中 ground truth 的报告计入误报侧的近似——见模块 docstring）
    - f1 = 2PR / (P + R)
    """
```

| 指标 | 口径 | 粒度 |
|---|---|---|
| Recall（检出率） | 检出案例数 / 总案例数 | 案例级（这个案例的缺陷抓到没有） |
| Precision（精确率，近似） | 命中发现数 / 报告发现数 | 发现级 |
| F1 | 调和平均 | 综合 |
| Token / 成本 / 耗时 | 各案例上报值汇总 | 运行级 |

Recall 用**案例级**口径（一个案例有一条命中即"检出"）——口径稳定、便于人工核对；代价是无法区分"部分命中"。对 5 个案例的种子集，这是合理简化。

### A/B 对比：逐案例找回归

`harness/compare.py` 的模块注释先列用途：

```python
"""A/B 对比：两次评测的回归报告。

典型用途：
- prompt 修改前后（结合 optim/prompts 的版本号）
- 错题本注入前后（验证自优化是否真的有效）
- 不同模型对比（deepseek-chat vs reasoner 的成本-质量曲线）
- 压缩阈值参数对比（60/80 vs 70/90 的取舍数据）

对比原则（面试要点）：同一数据集、同一模型、同一参数——
只变一个自变量，否则对比结果无法归因。
"""
```

实现不是"总分数并排放"，而是**逐案例差集**：

```python
    for case_id in sorted(set(cases_a) & set(cases_b)):
        ca, cb = cases_a[case_id], cases_b[case_id]
        if ca.get("detected") and not cb.get("detected"):
            regressions.append({"case_id": case_id, "detail": cb})
        elif not ca.get("detected") and cb.get("detected"):
            improvements.append({"case_id": case_id, "detail": cb})
```

`set(a) & set(b)` 取两轮都有的案例，比较 `detected` 布尔——**从"检出"变"漏报"的进 regressions，反向的进 improvements**。为什么追到案例级？改一版 prompt，总分可能微涨却被个别回归掩盖——**逐案例对比把那几条揪出来**。指标表则算 delta（数值差）：

```python
    metric_keys = ["cases", "detected", "recall", "precision", "f1",
                   "reported_total", "matched_total", "total_tokens",
                   "total_cost_yuan", "avg_cost_yuan"]
```

终端渲染（`_render_compare`）：指标 A→B 变化表 + 回归清单（红色）+ 改善清单（绿色）+ "逐案例结果无变化"的提示。**"只变一个自变量"**是这套设施的使用纪律——prompt 和模型同时换，回归了都不知道是谁的锅。

## 9.6 实测数据

确定性层基线（`--mock`，零成本，项目内 `.codesage-eval/baseline-final` 存档）：

| 配置 | 检出率 | 精确率* | F1 | 平均成本/案例 |
|---|---|---|---|---|
| 确定性层（mock） | **100%**（5/5） | 75% | **0.857** | ¥0 |
| 完整链路（LLM） | 待填（需 API Key 后运行 `codesage eval --tag v1-llm`） | | | |

12 条报告发现中 9 条命中（`reported_total: 12 / matched_total: 9`），5 个案例全部检出，整轮 11.6 秒。

**诚实标注**：完整 LLM 链路的评测数据还空着——需要在 `.env` 配好 API Key 后跑 `codesage eval --tag v1-llm` 取得。这是下一个待填的关键数据（也是 A/B 里的下一个自变量：确定性层 vs 完整链路，看模型多赚回多少检出、多花多少钱）。

另有一组靶场数据可以对照：`examples/demo_target`（6 个植入缺陷文件）跑 `--no-llm`，27 条发现、1.5 秒——**靶场与评测集的区别**：评测集测"该抓的抓到没有"（有 ground truth），靶场演示"能抓出什么"（无评分意义，纯展示）。

## 9.7 小结

- **两条命令路径**：评测模式（`--mock` / `--sample` / `--tag`）与对比模式（`--compare`），结果落 JSONL + summary；
- **三条纪律**：走真实链路（延迟导入 `run_review`）、隔离工作区（一案例一世界）、mock 可离线（零成本基线给 CI）；
- **数据集**：YAML 案例 = 片段 + 行区间 + 关键词 + 严重级；种子 5 个，扩充管道已写明（真实 bugfix 挖掘 → 25-30 个）；
- **软匹配三重条件**：文件名 + 区间重叠 ≥30%（分母用较短区间）+ 关键词任一命中；局限（precision 近似、关键词人工维护）主动标注；匹配器自身有 8 个测试；
- **A/B**：指标 delta + 逐案例回归/改善两个清单；纪律是"只变一个自变量"；
- **当前数据**：确定性层 100% / 75% / 0.857 / ¥0；LLM 全链路待跑。

支线一走完，下一个问题是"怎么让它越干越好"——错题本机制登场，那是**支线二**。

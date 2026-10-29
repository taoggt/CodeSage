# CodeSage 评测数据集

每个 `case_*.yaml` 描述一个"已知有缺陷"的代码片段（bugfix 场景的可复现版本）：

| 字段 | 说明 |
|---|---|
| `id` | 案例编号 |
| `category` | 对应审查维度：security / bug / performance / quality |
| `snippet` | 缺陷代码片段（相对本目录） |
| `ground_truth.line_range` | 缺陷所在行区间（1-based，含两端） |
| `ground_truth.keywords` | 软匹配关键词（finding 文本命中任一即算，用于规避措辞差异） |

## 运行

```bash
# mock 模式（只跑确定性层，无需 API Key）
codesage eval --mock --tag baseline-deterministic

# 完整模式（需要 .env 中配置 API Key）
codesage eval --tag v1-llm

# A/B 对比
codesage eval --compare baseline-deterministic,v1-llm
```

## 扩充数据集（Week 4 任务）

当前 5 个案例是手工构造的种子数据。扩充方式（真实 bug 挖掘管道）：

1. 选择 3-5 个活跃 Python 开源项目；
2. 筛选 commit message 含 fix / bug / security 的提交；
3. 取 parent 版本的相关文件片段 = buggy 代码（snippet）；
4. diff 与测试变更 → 推导 ground_truth 的 line_range 与 keywords；
5. 人工复核后放入本目录，目标 25-30 个案例（5 类 × 5-6 个）。

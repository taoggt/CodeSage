---
id: py-pythonic
title: Python 惯用法（Pythonic 风格）
tags: [惯用法, 可读性, BEST_PRACTICE]
triggers: [range, len, enumerate, zip, in dict, try, except, EAFP, LBYL, 字典, 循环, 下标, 索引]
---
## Python 惯用法对照

| 反模式 | 惯用写法 | 原因 |
|---|---|---|
| `for i in range(len(x))` | `for item in x:` / `enumerate(x)` | 直接迭代可读性更好，无索引开销 |
| `d.has_key(k)` / `if k in d.keys()` | `if k in d:` | 字典成员检查是 O(1)，.keys() 多一层视图 |
| 手动维护计数器 | `collections.Counter` | 标准库已实现，含 most_common 等能力 |
| 手动拼接路径 | `pathlib.Path` | 跨平台、可读性强 |
| 多个 if 判断 key 是否存在再取值 | `d.get(k, default)` | 一次查找，语义明确 |
| 用临时列表收集后再循环处理 | 生成器表达式 | 惰性求值，内存友好 |

### EAFP vs LBYL

Python 社区倾向 EAFP（Easier to Ask Forgiveness than Permission）：

```python
# 反例（LBYL：检查与使用之间存在竞态，且查找两次）
if key in mapping:
    value = mapping[key]

# 推荐（EAFP：一次查找，异常处理明确）
try:
    value = mapping[key]
except KeyError:
    value = default
```

注意：EAFP 不是教条——当"异常是常态"（如高频存在性判断）时，
`in` 检查反而更清晰。审查时结合调用频率与语义判断。

### 解包与多赋值

```python
# 推荐：解包代替下标
first, second = items[0], items[1]   # 反例
first, second = items                 # 推荐（长度已知时）
```

> 审查提示：惯用法建议要说明"为什么"——性能（复杂度变化）或
> 可读性（消除了什么噪音），不要只给结论。

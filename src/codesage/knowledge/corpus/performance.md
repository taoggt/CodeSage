---
id: py-performance
title: Python 性能反模式与优化
tags: [性能, 复杂度, PERFORMANCE]
triggers: [循环, 拼接, join, set, list, 成员检查, 重复计算, 属性查找, 生成器, 缓存, O(n), 复杂度]
---
## 性能优化检查清单

### 1. 字符串拼接

```python
# 反例：循环内 += 是 O(n²)（字符串不可变，每次创建新对象）
result = ""
for line in lines:
    result += line + "\n"

# 推荐
result = "\n".join(lines)
```

### 2. 成员检查的数据结构

```python
# 反例：列表线性查找 O(n)
if status in ["active", "pending", "frozen"]: ...

# 推荐：集合哈希查找 O(1)
if status in {"active", "pending", "frozen"}: ...
```

### 3. 循环不变量外提

```python
# 反例：循环内重复属性查找与重复计算
for item in items:
    if item.score > self.config.threshold * self.factor: ...

# 推荐：不变的部分提到循环外
threshold = self.config.threshold * self.factor
for item in items:
    if item.score > threshold: ...
```

### 4. 生成器 vs 列表

```python
# 只需要遍历一次时，不要物化整个列表
total = sum(int(x) for x in data)          # 推荐
total = sum([int(x) for x in data])        # 反例：多一次完整列表分配
```

### 5. 复杂度量级判断（审查时优先关注）

- O(n²) 及以上：嵌套循环处理同一数据集、循环内调用 O(n) 操作
  （in list / list.index / list.remove）；
- 循环内 I/O：数据库查询、网络请求应批量化（N+1 问题）；
- 重复计算：同一纯函数在同一数据上多次调用（考虑缓存或局部变量）。

> 审查提示：性能建议必须给出量级变化（O(n²) → O(n)）或
> 具体数字（如"减少 N 次查询"），不给量级的性能建议是无价值的。

---
id: py-resources
title: 资源管理与上下文管理器
tags: [资源管理, BUG, 文件句柄]
triggers: [open, close, with, 文件, 句柄, 连接, 泄漏, contextlib, finally, lock, 锁]
---
## 资源管理检查清单

### 1. 文件句柄：始终用 with

```python
# 反例：异常路径下句柄不会关闭
f = open(path)
data = f.read()      # 这里抛异常 → f 永久泄漏
f.close()

# 推荐：with 保证任何路径都关闭
with open(path) as f:
    data = f.read()
```

### 2. 其他需要 with 的资源

- 数据库连接 / 游标（注意：连接池场景由池负责归还，语义不同）
- 网络 socket
- 线程锁（`with lock:` 保证异常时释放）
- 临时文件（`tempfile.TemporaryDirectory`）

### 3. 自定义上下文管理器

```python
from contextlib import contextmanager

@contextmanager
def timed(label: str):
    start = time.perf_counter()
    try:
        yield
    finally:
        logger.info("%s 耗时 %.3fs", label, time.perf_counter() - start)
```

### 4. 识别"疑似泄漏"的边界（避免误报）

以下情况**不算**泄漏，审查时不要报告：

- `open()` 作为参数直接传入其他托管函数（如 `pandas.read_csv(open(p))`
  是坏味道但不必然泄漏——read_csv 不负责关闭）；
- 进程级短生命周期脚本中未关闭的句柄（进程退出即释放，
  但依赖此行为仍是坏习惯，可作为 INFO 提示）；
- 连接池管理的连接（`pool.getconn()` 有配套归还逻辑）。

> 审查提示：报告资源泄漏必须能指出"哪条异常路径会跳过 close"，
> 只看到没有 with 就报告属于低质量发现。

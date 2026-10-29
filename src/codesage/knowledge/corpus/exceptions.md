---
id: py-exceptions
title: 异常处理规范
tags: [异常处理, 健壮性, BUG]
triggers: [except, raise, try, finally, except Exception, 裸 except, 吞异常, 异常链, from]
---
## 异常处理检查清单

### 1. 禁止裸 except / 静默吞异常

```python
# 反例：捕获一切（包括 KeyboardInterrupt），且不知道发生了什么
try:
    process()
except:
    pass

# 推荐：具体类型 + 记录上下文
try:
    process()
except (ValueError, OSError) as exc:
    logger.exception("处理失败 user=%s", user_id)
    raise
```

### 2. 异常链：raise ... from

转换异常类型时保留原始堆栈：

```python
try:
    data = json.loads(raw)
except json.JSONDecodeError as exc:
    raise ConfigError("配置格式非法") from exc   # 保留原始因果链
```

### 3. finally 与资源

`finally` 中不要 `return`（会吞掉正在传播的异常）。
资源释放优先用 `with`（详见资源管理卡片）。

### 4. 空 except 的合理场景（少见但存在）

- 清理逻辑：`try: cleanup() except Exception: pass` —— 必须是
  明确的"清理失败不应中断主流程"，且要有注释说明；
- 存在性探测（如 `try: os.remove(p) except FileNotFoundError: pass`）。

> 审查提示：判断"吞异常"要区分场景——测试代码和清理路径中的
> 宽捕获可能是刻意的；业务路径中的宽捕获才是风险点。

---
id: py-security
title: Python 安全编码要点（OWASP 语境）
tags: [安全, 注入, SECURITY]
triggers: [SQL 注入, sql, execute, subprocess, shell, pickle, yaml.load, eval, 密钥, 密码, 反序列化, 路径穿越, 命令注入]
---
## Python 常见安全风险与修复

### 1. SQL 注入 —— 参数化查询（唯一正解）

```python
# 反例：三种拼接方式都危险
cursor.execute(f"SELECT * FROM users WHERE name = '{name}'")
cursor.execute("SELECT * FROM users WHERE name = '%s'" % name)
cursor.execute("SELECT * FROM users WHERE name = '" + name + "'")

# 推荐：占位符由驱动负责转义
cursor.execute("SELECT * FROM users WHERE name = ?", (name,))
```

注意：表名/列名不能参数化——必须用白名单映射，禁止拼接用户输入。

### 2. 命令注入

```python
# 反例
os.system(f"convert {user_path} out.png")
subprocess.run(f"git log {branch}", shell=True)

# 推荐：参数列表 + 不经过 shell
subprocess.run(["convert", user_path, "out.png"], check=True)
```

### 3. 反序列化

- `pickle` / `marshal`：只用于可信数据，禁止用于网络/用户输入；
- `yaml.load`：必须用 `yaml.safe_load`；
- `eval` / `exec`：解析字面量用 `ast.literal_eval`。

### 4. 密钥管理

```python
# 反例：密钥随代码进入版本库
API_KEY = "sk-live-xxxxxxxx"

# 推荐：环境变量 / 密钥管理服务
API_KEY = os.environ["API_KEY"]
```

### 5. 路径穿越

```python
# 反例：用户可控相对路径 + 拼接
target = os.path.join(BASE, user_input)   # ../../etc/passwd

# 推荐：resolve 后校验仍在基目录内
target = (BASE / user_input).resolve()
if not target.is_relative_to(BASE.resolve()):
    raise ValueError("非法路径")
```

> 审查提示：安全问题的报告要给出"触发路径"（什么输入 → 什么后果），
> 只说"存在风险"没有说服力。

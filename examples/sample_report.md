# CodeSage 审查报告：D:\WorkBuddyWorkingDir\CodeSage\examples\eval_dataset\snippets

**评分：51 / 100**

文件 5 个 · 耗时 1.4s

| P0 | P1 | P2 | P3 |
|---|---|---|---|
| 0 | 3 | 8 | 1 |

## 🟠 P1 可变默认参数：load_configs()

- **位置**：`case_002.py:5`（类别：潜在 Bug）
- **来源**：规则 · CG001 · 置信度 100%

Python 的默认参数只在函数定义时求值一次，可变默认值会被所有调用共享，导致跨调用的状态污染。

```python
def load_configs(paths, cache=[]):
```

> **建议**：改用 None 作默认值，函数内部按需创建：`if x is None: x = []`

## 🟠 P1 裸 except（except:）捕获全部异常

- **位置**：`case_004.py:9-10`（类别：潜在 Bug）
- **来源**：规则 · CG002 · 置信度 100%

裸 except 会捕获包括 KeyboardInterrupt、SystemExit 在内的所有异常，可能掩盖真正的错误，也妨碍程序正常退出。

```python
except:
```

> **建议**：捕获具体异常类型，如 `except ValueError as exc:`；确需兜底时用 `except Exception:`

## 🟠 P1 [B602] subprocess_popen_with_shell_equals_true

- **位置**：`case_005.py:8`（类别：安全）
- **来源**：Bandit · B602 · 置信度 95%

subprocess call with shell=True identified, security issue.

```python
7     cmd = f"convert {user_path} out.png"
8     subprocess.run(cmd, shell=True)
```

> **建议**：请查阅 Bandit 文档对应规则的安全修复建议

## 🟡 P2 [B608] hardcoded_sql_expressions

- **位置**：`case_001.py:9`（类别：安全）
- **来源**：Bandit · B608 · 置信度 60%

Possible SQL injection vector through string-based query construction.

```python
8     cursor = conn.cursor()
9     query = f"SELECT id, name FROM users WHERE name = '{username}'"
10     cursor.execute(query)
```

> **建议**：请查阅 Bandit 文档对应规则的安全修复建议

## 🟡 P2 open() 未使用 with 语句

- **位置**：`case_002.py:10`（类别：潜在 Bug）
- **来源**：规则 · CG005 · 置信度 60%

open() 返回的文件句柄未在 with 块中管理，若异常路径未 close 会导致资源泄漏。（若已手动 close，请在复核时忽略本条）

```python
f = open(path, encoding="utf-8")
```

> **建议**：改用 `with open(path) as f:`，异常路径也能保证关闭

## 🟡 P2 循环内使用 += 拼接字符串

- **位置**：`case_003.py:8`（类别：性能）
- **来源**：规则 · CG007 · 置信度 80%

字符串不可变，循环内累加会反复创建新对象，复杂度 O(n²)。

```python
text += f"{row['name']}: {row['value']}\n"
```

> **建议**：改为收集到列表再 "".join(parts)

## 🟡 P2 [F401] `os` imported but unused

- **位置**：`case_004.py:2`（类别：代码质量）
- **来源**：Ruff · F401 · 置信度 90%

Ruff 规则 F401：`os` imported but unused

> **建议**：运行 `ruff check --fix` 可自动修复部分问题

## 🟡 P2 公共符号缺少 docstring：read_lines

- **位置**：`case_004.py:5-10`（类别：代码质量）
- **来源**：规则 · CG006 · 置信度 90%

缺少文档会影响可维护性与 IDE 提示质量。

```python
def read_lines(path):
```

> **建议**：补充一行式 docstring："""一句话说明职责。"""

## 🟡 P2 open() 未使用 with 语句

- **位置**：`case_004.py:7`（类别：潜在 Bug）
- **来源**：规则 · CG005 · 置信度 60%

open() 返回的文件句柄未在 with 块中管理，若异常路径未 close 会导致资源泄漏。（若已手动 close，请在复核时忽略本条）

```python
f = open(path)
```

> **建议**：改用 `with open(path) as f:`，异常路径也能保证关闭

## 🟡 P2 [E722] Do not use bare `except`

- **位置**：`case_004.py:9`（类别：代码质量）
- **来源**：Ruff · E722 · 置信度 90%

Ruff 规则 E722：Do not use bare `except`

> **建议**：运行 `ruff check --fix` 可自动修复部分问题

## 🟡 P2 使用 == 比较 None

- **位置**：`case_004.py:14`（类别：代码质量）
- **来源**：规则 · CG004 · 置信度 100%

None 是单例对象，身份比较（is / is not）更准确也更快。

```python
if value == None:
```

> **建议**：改为 `is None` / `is not None`

## 🔵 P3 [B404] blacklist

- **位置**：`case_005.py:2`（类别：安全）
- **来源**：Bandit · B404 · 置信度 95%

Consider possible security implications associated with the subprocess module.

```python
1 """文件处理（评测案例：命令注入）。"""
2 import subprocess
3
```

> **建议**：请查阅 Bandit 文档对应规则的安全修复建议

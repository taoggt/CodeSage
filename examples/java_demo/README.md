# Java 演示靶场

用于验证 CodeSage 的 Java 审查链路（`--no-llm` 即可跑，零 Token）：

```bash
codesage review examples/java_demo --no-llm
```

## 文件与预期命中

| 文件 | 预期规则 | 说明 |
|---|---|---|
| `src/main/java/com/demo/OrderService.java` | JG001 ×2、JG002 ×2、JG004、JG009 | 空 catch（含只有注释的）、宽捕获 Exception/Throwable、硬编码凭据、equals 无 hashCode |
| `src/main/java/com/demo/CommandRunner.java` | JG003 ×3 | 动态 `exec`（MAJOR）、字面量 `exec`（INFO）、`ProcessBuilder` 动态参数（MAJOR） |
| `src/main/java/com/demo/UserRepository.java` | JG005 ×2、JG006 | 直接拼接 SQL、先拼到变量再执行、`FileInputStream` 未用 try-with-resources |
| `src/main/java/com/demo/ReportBuilder.java` | JG007、JG008、JGCC | 循环内 `+=`、static `SimpleDateFormat`、`classify` 圈复杂度 ≥ 10 |
| `src/test/java/com/demo/OrderServiceTest.java` | JG001、JG002 | **测试豁免演示**：JG004 与 JGCC 在测试代码里跳过，空 catch / 宽捕获不跳过 |

## 规则清单

| ID | 检测内容 | 类别 / 级别 | 对应 Python 规则 |
|---|---|---|---|
| JG001 | 空 catch / 只有注释的 catch | bug / major | CG003 |
| JG002 | catch Exception / Throwable / RuntimeException | bug·best_practice / major·minor | CG010 |
| JG003 | `Runtime.exec` / `ProcessBuilder` | security / major | CG013 |
| JG004 | 硬编码凭据 | security / major | CG012 |
| JG005 | SQL 字符串拼接 | security / major | — |
| JG006 | 资源未用 try-with-resources | bug / minor | CG005 |
| JG007 | 循环内字符串 `+=` | performance / minor | CG007 |
| JG008 | static 的 `SimpleDateFormat` | bug / major | — |
| JG009 | 重写 equals 未重写 hashCode | bug / minor | — |
| JGCC | 圈复杂度（阈值同 radon：10 / 20） | performance / minor·major | radon |

## 已知边界（阶段一不做）

需要类型解析或符号表才能判断的问题不在确定性层范围内，交给 LLM 复核或阶段二的 JVM 工具：

- `@Transactional` 方法内自调用失效（需代理对象语义 + Spring 配置）
- NPE 数据流、未使用 import、循环依赖
- 跨方法追踪（`String sql = ...` 在 A 方法拼、B 方法执行）

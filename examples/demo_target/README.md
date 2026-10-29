# 演示靶场（demo_target）

⚠️ **本目录的代码故意包含缺陷**——安全漏洞、逻辑 Bug、性能反模式——仅用于演示与验证 CodeSage 的检测能力。**请勿复制到任何真实项目。**

## 故意注入的缺陷清单

| 文件 | 注入的缺陷 | 预期检出方式 |
|---|---|---|
| `user_service.py` | f-string/拼接 SQL 注入、硬编码密钥 | Bandit B608 · 规则 CG012 |
| `order_processor.py` | 可变默认参数、裸 except、异常吞没、== None、range(len) | 规则 CG001/CG002/CG003/CG004/CG009 |
| `report_utils.py` | 循环内字符串拼接、列表成员检查 | 规则 CG007/CG008 |
| `file_utils.py` | open() 未使用 with（资源泄漏） | 规则 CG005 |
| `admin_tools.py` | os.system 命令注入、subprocess shell=True、eval 代码执行 | 规则 CG011/CG013 · Bandit B605/B602/B307 |
| `data_sync.py` | 高圈复杂度函数、过宽的 except Exception | radon · 规则 CG010 |

## 运行验证

```bash
codesage review examples/demo_target --no-llm
```

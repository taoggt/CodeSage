"""CodeSage 异常体系。

设计原则：所有自定义异常继承 CodeSageError，CLI 层统一捕获并友好展示；
内部模块抛出语义化异常（如 LLMNotConfiguredError），而不是裸 Exception。
"""


class CodeSageError(Exception):
    """所有 CodeSage 异常的基类。"""


class ConfigError(CodeSageError):
    """配置加载或校验失败。"""


class LLMError(CodeSageError):
    """LLM 调用失败的通用异常。"""


class LLMNotConfiguredError(LLMError):
    """未配置 API Key，无法调用 LLM。

    提示：可运行 `codesage review --no-llm` 走纯确定性分析模式。
    """


class BudgetExceededError(CodeSageError):
    """Token 预算熔断。"""


class ToolExecutionError(CodeSageError):
    """工具执行失败（会被回填给模型自我纠正，通常不向上抛出）。"""


class IngestError(CodeSageError):
    """输入采集阶段失败（扫描 / 切块 / diff 解析）。"""


class HarnessError(CodeSageError):
    """评测框架相关错误。"""

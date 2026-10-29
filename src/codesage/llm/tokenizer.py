"""Token 估算器：预算控制的基础设施。

设计取舍（为什么默认不用 tiktoken）：
- tiktoken 首次运行需要联网下载 BPE 编码文件，国内环境容易卡住；
- 预算控制只需要 ±20% 量级的精度（熔断阈值留有安全余量），
  启发式估算完全够用；
- 抽象出 TokenEstimator 接口，需要高精度时一行切换（prefer_tiktoken=True）。
"""

from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class TokenEstimator(Protocol):
    """估算接口。"""

    def estimate(self, text: str) -> int:
        """估算一段文本的 token 数。"""
        ...


class HeuristicEstimator:
    """启发式估算器（零依赖）。

    规则（对 GPT/DeepSeek 系 BPE 分词的经验近似）：
    - CJK 字符：约 1 字符 = 1 token
    - 其余字符（英文/代码/符号）：约 3.5 字符 = 1 token

    实际误差通常在 ±20% 以内，用于“何时触发压缩/熔断”的决策足够。
    """

    CJK_RANGE = ("\u4e00", "\u9fff")

    def estimate(self, text: str) -> int:
        if not text:
            return 0
        cjk = 0
        for ch in text:
            if self.CJK_RANGE[0] <= ch <= self.CJK_RANGE[1]:
                cjk += 1
        other = len(text) - cjk
        return int(cjk + other / 3.5) + 1

    def estimate_messages(self, messages: list[dict[str, Any]]) -> int:
        """估算完整消息列表（含角色开销与 tool_calls 的 JSON 体积）。

        每条消息额外 +4 token（OpenAI 消息包装开销的经验值），
        整个请求再 +3。
        """
        total = 3
        for msg in messages:
            total += 4
            content = msg.get("content")
            if isinstance(content, str):
                total += self.estimate(content)
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                try:
                    total += self.estimate(json.dumps(tool_calls, ensure_ascii=False))
                except (TypeError, ValueError):
                    total += 50
            name = msg.get("name")
            if isinstance(name, str):
                total += self.estimate(name)
        return total


class TiktokenEstimator:
    """可选高精度估算器（需要自行 pip install tiktoken）。"""

    def __init__(self, encoding_name: str = "cl100k_base") -> None:
        import tiktoken  # 延迟导入：未安装时不报错，只有显式使用时才要求

        self._enc = tiktoken.get_encoding(encoding_name)

    def estimate(self, text: str) -> int:
        if not text:
            return 0
        return len(self._enc.encode(text))

    def estimate_messages(self, messages: list[dict[str, Any]]) -> int:
        total = 3
        for msg in messages:
            total += 4
            content = msg.get("content")
            if isinstance(content, str):
                total += self.estimate(content)
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                try:
                    total += self.estimate(json.dumps(tool_calls, ensure_ascii=False))
                except (TypeError, ValueError):
                    total += 50
        return total


def get_estimator(prefer_tiktoken: bool = False) -> TokenEstimator:
    """获取估算器：默认启发式，可尝试升级为 tiktoken。"""
    if prefer_tiktoken:
        try:
            return TiktokenEstimator()
        except Exception:  # noqa: BLE001 - 任何失败都优雅回退
            pass
    return HeuristicEstimator()

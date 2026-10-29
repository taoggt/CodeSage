"""LLM 客户端：openai SDK 的薄封装（OpenAI 兼容协议）。

设计要点：
- 只暴露 CodeSage 自己的 LLMResponse / ToolCall 数据结构，
  openai SDK 的类型不泄漏到 Agent 层（可测试、可替换）；
- 消息格式直接使用 OpenAI 的 dict 协议（role/content/tool_calls/tool_call_id），
  因为自研 ReAct 循环原样维护它，无需转换；
- 惰性初始化底层 client，未配置 API Key 时给出明确错误。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from codesage.core.config import LLMSettings
from codesage.core.errors import LLMError, LLMNotConfiguredError
from codesage.core.models import TokenUsage


@dataclass
class ToolCall:
    """模型请求的一次工具调用。"""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    raw_arguments: str = ""
    parse_error: str = ""


@dataclass
class LLMResponse:
    """统一响应结构。"""

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=TokenUsage)
    model: str = ""

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


class LLMClient:
    """OpenAI 兼容协议的同步客户端。"""

    def __init__(self, settings: LLMSettings) -> None:
        self._settings = settings
        self._client: Any = None

    @property
    def configured(self) -> bool:
        return bool(self._settings.api_key.strip())

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.configured:
            raise LLMNotConfiguredError(
                "未配置 LLM API Key。请复制 .env.example 为 .env 并填入密钥，"
                "或使用 `codesage review --no-llm` 运行纯确定性分析。"
            )
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise LLMError("缺少依赖 openai，请先执行 pip install -e .") from exc
        self._client = OpenAI(
            api_key=self._settings.api_key,
            base_url=self._settings.base_url,
            timeout=self._settings.timeout,
            max_retries=2,  # SDK 自带指数退避重试
        )
        return self._client

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        """执行一次对话补全（可能包含工具调用）。

        messages 采用 OpenAI dict 协议，Agent 循环原样维护。
        """
        client = self._ensure_client()
        kwargs: dict[str, Any] = {
            "model": model or self._settings.model_strong,
            "messages": messages,
            "temperature": (
                temperature if temperature is not None else self._settings.temperature
            ),
            "max_tokens": self._settings.max_output_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        try:
            resp = client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - 统一包装为 LLMError
            raise LLMError(f"LLM 调用失败：{exc}") from exc

        choice = resp.choices[0] if resp.choices else None
        if choice is None:  # pragma: no cover
            raise LLMError("LLM 返回了空 choices")

        message = choice.message
        tool_calls: list[ToolCall] = []
        for tc in (message.tool_calls or []):
            raw = tc.function.arguments or "{}"
            arguments: dict[str, Any] = {}
            parse_error = ""
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    arguments = parsed
                else:
                    parse_error = f"参数不是 JSON 对象：{raw[:120]}"
            except json.JSONDecodeError as exc:
                parse_error = f"参数 JSON 解析失败：{exc}"
            tool_calls.append(
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=arguments,
                    raw_arguments=raw,
                    parse_error=parse_error,
                )
            )

        usage = TokenUsage()
        if resp.usage is not None:
            usage = TokenUsage(
                input_tokens=resp.usage.prompt_tokens or 0,
                output_tokens=resp.usage.completion_tokens or 0,
                calls=1,
            )
        else:
            usage = TokenUsage(calls=1)

        return LLMResponse(
            content=(message.content or "").strip(),
            tool_calls=tool_calls,
            usage=usage,
            model=kwargs["model"],
        )


class MockLLMClient:
    """脚本化 Mock 客户端：按预置响应序列返回，用尽后返回空响应。

    用途：
    - 单元测试（不依赖网络与 API Key）
    - `codesage review --mock` 演示模式
    """

    def __init__(self, responses: list[LLMResponse] | None = None) -> None:
        self._responses = list(responses or [])
        self.calls: list[list[dict[str, Any]]] = []  # 记录每次请求，便于断言

    @property
    def configured(self) -> bool:
        return True

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        self.calls.append([dict(m) for m in messages])
        if self._responses:
            resp = self._responses.pop(0)
            resp.model = model or resp.model or "mock"
            return resp
        return LLMResponse(content="（mock 响应已用尽）", model="mock")


def assistant_message_with_tool_calls(resp: LLMResponse) -> dict[str, Any]:
    """把 LLMResponse 中的 tool_calls 转成 OpenAI 协议消息（用于回填历史）。"""
    return {
        "role": "assistant",
        "content": resp.content or None,
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": tc.raw_arguments or "{}"},
            }
            for tc in resp.tool_calls
        ],
    }


def tool_result_message(tool_call_id: str, content: str) -> dict[str, Any]:
    """构造工具结果消息（role=tool）。"""
    return {"role": "tool", "tool_call_id": tool_call_id, "content": content}

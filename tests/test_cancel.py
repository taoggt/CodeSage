"""取消语义测试：扫描/静态阶段取消不落盘，LLM 阶段取消保留部分结果。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from codesage.agent.loop import AgentLoop, LoopConfig
from codesage.agent.policies import StopPolicy
from codesage.agent.registry import ToolRegistry, tool
from codesage.core.cancel import CancelToken
from codesage.core.config import BudgetSettings, Settings
from codesage.llm.budget import TokenBudget
from codesage.llm.client import LLMResponse, ToolCall
from codesage.llm.tokenizer import HeuristicEstimator
from codesage.memory.working import WorkingMemory
from codesage.pipeline import ReviewOptions, run_review


def test_cancel_token_semantics() -> None:
    token = CancelToken()
    assert not token.cancelled
    token.cancel()
    assert token.cancelled
    token.reset()
    assert not token.cancelled


def test_run_review_cancelled_before_start(workspace: Path, settings: Settings) -> None:
    token = CancelToken()
    token.cancel()
    report = run_review(
        workspace, settings, ReviewOptions(use_llm=False, cancel_token=token)
    )
    assert report.metadata["cancelled"] is True
    assert report.metadata["cancelled_phase"] == "init"
    assert "review_id" not in report.metadata  # 未落盘
    assert report.findings == []


def test_cancel_after_scan_phase_skips_persist(
    workspace: Path, settings: Settings
) -> None:
    """扫描结束、静态分析开始前取消：阶段记为 scan，且不落盘。

    语义说明：检查点在阶段边界上，所以"刚扫完就取消"归到 scan 阶段，
    而不是假装静态分析跑过一部分。
    """
    token = CancelToken()

    def on_event(event: str, payload: dict) -> None:
        if event == "scan":
            token.cancel()

    report = run_review(
        workspace,
        settings,
        ReviewOptions(use_llm=False, cancel_token=token, on_event=on_event),
    )
    assert report.metadata["cancelled"] is True
    assert report.metadata["cancelled_phase"] == "scan"
    assert report.findings == []
    assert "review_id" not in report.metadata

    from codesage.memory.store import Store

    store = Store(Path(settings.review.store_path))
    try:
        assert store.review_count() == 0
    finally:
        store.close()


class _CancelAfterFirstChat:
    """第一次调用后置位取消令牌，模拟"用户在 Agent 思考时点了停止"。"""

    def __init__(self, token: CancelToken, response: LLMResponse) -> None:
        self._token = token
        self._response = response
        self.calls = 0

    def chat(self, messages, tools=None, model=None, temperature=None):  # noqa: ANN001
        self.calls += 1
        self._token.cancel()
        return self._response


def _echo_registry() -> ToolRegistry:
    registry = ToolRegistry()

    @tool(name="echo")
    def echo(text: str) -> str:
        """回显。

        Args:
            text: 文本
        """
        return text

    registry.register(echo)
    return registry


def test_loop_cancel_mid_run_keeps_status() -> None:
    token = CancelToken()
    tool_call = ToolCall(id="t1", name="echo", arguments={"text": "hi"}, raw_arguments=json.dumps({"text": "hi"}))
    response = LLMResponse(content="先读一下文件", tool_calls=[tool_call])
    client = _CancelAfterFirstChat(token, response)

    loop = AgentLoop(
        client=client,
        registry=_echo_registry(),
        memory=WorkingMemory(active_window_turns=4),
        budget=TokenBudget(BudgetSettings()),
        estimator=HeuristicEstimator(),
        model="mock",
        policy=StopPolicy(max_turns=5),
        config=LoopConfig(),
        cancel_token=token,
    )
    result = loop.run([{"role": "user", "content": "开始"}])

    assert result.status == "cancelled"
    assert result.stop_reason == "用户取消"
    assert result.turns <= 1
    assert client.calls == 1  # 取消后不再发起第二次调用


def test_loop_emits_assistant_message_and_tool_args() -> None:
    token = CancelToken()
    tool_call = ToolCall(id="t1", name="echo", arguments={"text": "hi"}, raw_arguments="{}")
    responses = [LLMResponse(content="我要调用工具", tool_calls=[tool_call]), LLMResponse(content="完成")]
    from codesage.llm.client import MockLLMClient

    events: list[tuple[str, dict[str, Any]]] = []
    loop = AgentLoop(
        client=MockLLMClient(responses),
        registry=_echo_registry(),
        memory=WorkingMemory(active_window_turns=4),
        budget=TokenBudget(BudgetSettings()),
        estimator=HeuristicEstimator(),
        model="mock",
        policy=StopPolicy(max_turns=5),
        config=LoopConfig(),
        on_event=lambda name, payload: events.append((name, payload)),
        cancel_token=token,
    )
    loop.run([{"role": "user", "content": "开始"}])

    names = [name for name, _ in events]
    assert "assistant_message" in names
    assert "tool_calls" in names
    tool_calls_payload = next(payload for name, payload in events if name == "tool_calls")
    assert tool_calls_payload["names"] == ["echo"]
    assert tool_calls_payload["args"][0]["name"] == "echo"

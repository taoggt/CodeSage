"""Agent 循环测试：Mock 客户端驱动，不依赖网络与 API Key。

覆盖（对应规划文档的循环设计要点）：
- 工具调用 → 结果回填 → 正常结束
- 未知工具 / 参数错误 → ERROR 文本回填（模型可自纠）
- 轮次上限 / 无进展 → 策略停止
- Token 预算 → 熔断
- 软阈值 → 回合边界压缩
"""

from __future__ import annotations

import json

from codesage.agent.loop import AgentLoop, LoopConfig
from codesage.agent.policies import StopPolicy
from codesage.agent.registry import ToolRegistry, tool
from codesage.core.config import BudgetSettings
from codesage.llm.budget import TokenBudget
from codesage.llm.client import LLMResponse, MockLLMClient, ToolCall
from codesage.llm.tokenizer import HeuristicEstimator
from codesage.memory.working import WorkingMemory


def make_registry():
    registry = ToolRegistry()
    seen: list[str] = []

    @tool(name="echo")
    def echo(text: str) -> str:
        """回显文本。

        Args:
            text: 要回显的文本
        """
        seen.append(text)
        return f"echo:{text}"

    registry.register(echo)
    return registry, seen


def tc(name: str = "echo", args: dict | None = None, tid: str = "tc1") -> ToolCall:
    args = args if args is not None else {"text": "hi"}
    return ToolCall(id=tid, name=name, arguments=args, raw_arguments=json.dumps(args))


def build_loop(
    responses,
    *,
    max_turns: int = 5,
    context_window: int = 64000,
    active_window: int = 4,
    findings_counter=None,
    compressor=None,
):
    registry, seen = make_registry()
    client = MockLLMClient(list(responses))
    budget = TokenBudget(BudgetSettings(context_window=context_window))
    memory = WorkingMemory(active_window_turns=active_window)
    loop = AgentLoop(
        client=client,
        registry=registry,
        memory=memory,
        budget=budget,
        estimator=HeuristicEstimator(),
        model="mock",
        policy=StopPolicy(max_turns=max_turns, no_progress_patience=2),
        config=LoopConfig(),
        findings_counter=findings_counter,
        compressor=compressor,
    )
    return loop, client, seen


def base_messages(system_content: str = "系统提示"):
    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": "任务"},
    ]


# ---------------------------------------------------------------- 基础流程


def test_finishes_without_tool_calls():
    loop, client, seen = build_loop([LLMResponse(content="完成")])
    result = loop.run(base_messages())
    assert result.status == "finished"
    assert result.turns == 0
    assert seen == []


def test_tool_executed_and_result_fed_back():
    loop, client, seen = build_loop(
        [
            LLMResponse(content="", tool_calls=[tc()]),
            LLMResponse(content="完成"),
        ]
    )
    result = loop.run(base_messages())
    assert result.status == "finished"
    assert seen == ["hi"]
    assert result.turns == 1
    assert result.tool_calls_total == 1

    second_request = client.calls[1]
    # assistant 消息（带 tool_calls）与 tool 结果都已回填
    assert any(
        m.get("role") == "assistant" and m.get("tool_calls") for m in second_request
    )
    tool_msgs = [m for m in second_request if m.get("role") == "tool"]
    assert tool_msgs and "echo:hi" in tool_msgs[0]["content"]


# ---------------------------------------------------------------- 错误回填（自我纠正）


def test_unknown_tool_error_fed_back():
    loop, client, seen = build_loop(
        [
            LLMResponse(content="", tool_calls=[tc(name="not_exist", args={})]),
            LLMResponse(content="完成"),
        ]
    )
    result = loop.run(base_messages())
    assert result.status == "finished"
    tool_msgs = [m for m in client.calls[1] if m.get("role") == "tool"]
    assert tool_msgs and "ERROR" in tool_msgs[0]["content"]


def test_missing_argument_error_fed_back():
    loop, client, seen = build_loop(
        [
            LLMResponse(content="", tool_calls=[tc(args={})]),  # 缺少 text 参数
            LLMResponse(content="完成"),
        ]
    )
    loop.run(base_messages())
    tool_msgs = [m for m in client.calls[1] if m.get("role") == "tool"]
    assert tool_msgs and "ERROR" in tool_msgs[0]["content"]


def test_tool_exception_error_fed_back():
    registry = ToolRegistry()

    @tool(name="boom")
    def boom() -> str:
        """总是抛异常的工具。"""
        raise RuntimeError("内部错误")

    registry.register(boom)
    client = MockLLMClient(
        [
            LLMResponse(content="", tool_calls=[tc(name="boom", args={})]),
            LLMResponse(content="完成"),
        ]
    )
    loop = AgentLoop(
        client=client,
        registry=registry,
        memory=WorkingMemory(),
        budget=TokenBudget(BudgetSettings()),
        estimator=HeuristicEstimator(),
        model="mock",
    )
    result = loop.run(base_messages())
    assert result.status == "finished"  # 回填错误而非崩溃
    tool_msgs = [m for m in client.calls[1] if m.get("role") == "tool"]
    assert tool_msgs and "ERROR" in tool_msgs[0]["content"]


# ---------------------------------------------------------------- 停止条件


def test_max_turns_stops():
    repeat = [LLMResponse(content="", tool_calls=[tc()])] * 10
    loop, client, seen = build_loop(repeat, max_turns=3)
    result = loop.run(base_messages())
    assert result.status == "stopped"
    assert "轮次上限" in result.stop_reason
    assert result.turns == 3


def test_no_progress_zero_submit_has_grace_period():
    """零提交场景有宽限期（覆盖调查阶段），宽限用尽后停止。

    教训：真实案例中 LLM 在前 3 轮"调查期"（读码/跑分析/查知识库）
    尚未到提交时机，被旧逻辑（turn=3 即停）掐断。
    """
    repeat = [LLMResponse(content="", tool_calls=[tc()])] * 12
    loop, client, seen = build_loop(
        repeat, max_turns=12, findings_counter=lambda: 0
    )
    result = loop.run(base_messages())
    assert result.status == "stopped"
    assert "未提交任何发现" in result.stop_reason
    assert result.turns == 5  # patience(2)+2 宽限 = 4 轮全零 + 第 5 轮判定


def test_no_progress_after_findings_stops():
    """提交过发现后的停滞 → 按常规耐心值停止。"""
    sequence = iter([0, 0, 0, 5, 5, 5, 5, 5, 5, 5])
    repeat = [LLMResponse(content="", tool_calls=[tc()])] * 10
    loop, client, seen = build_loop(
        repeat, max_turns=10, findings_counter=lambda: next(sequence)
    )
    result = loop.run(base_messages())
    assert result.status == "stopped"
    assert "无新增发现" in result.stop_reason
    assert result.turns == 6


def test_budget_abort():
    # 极小上下文窗口 → 第一轮检查即熔断
    loop, client, seen = build_loop(
        [LLMResponse(content="", tool_calls=[tc()])],
        context_window=10,
    )
    result = loop.run(base_messages())
    assert result.status == "aborted"
    assert "熔断" in result.stop_reason


# ---------------------------------------------------------------- 记忆压缩


def test_soft_threshold_triggers_compression():
    """软阈值在回合边界触发压缩（不打断推理）。

    参数设计（让估算可控）：
    - system 内容 3000 字符 ≈ 858 tokens，加上消息开销 ≈ 870；
    - context_window=1500 → soft=900 / hard=1200 / abort=1425；
    - 每轮工具结果 100 字符 ≈ 35 tokens；
    - 第 2 轮末 ≈ 990 tokens → SOFT，且此时活跃区已有 2 轮（window=1）→ 压缩执行。
    """
    compressed_prompts: list[str] = []

    def fake_compressor(prompt: str) -> str:
        compressed_prompts.append(prompt)
        return "压缩摘要"

    responses = [
        LLMResponse(content="", tool_calls=[tc(args={"text": "x" * 100})]),
        LLMResponse(content="", tool_calls=[tc(args={"text": "x" * 100})]),
        LLMResponse(content="完成"),
    ]
    loop, client, seen = build_loop(
        responses,
        max_turns=6,
        context_window=1500,
        active_window=1,
        compressor=fake_compressor,
    )
    result = loop.run(base_messages(system_content="x" * 3000))
    assert result.compress_events >= 1
    assert compressed_prompts  # 压缩器被调用
    # 新契约：权威事实由钉住块确定性重建，压缩 prompt 明确不接管它们
    assert "已提交发现" in compressed_prompts[0]
    assert "已读文件清单" in compressed_prompts[0]
    assert result.status in ("finished", "stopped")


def test_compression_skipped_without_compressor():
    repeat = [LLMResponse(content="", tool_calls=[tc()])] * 6
    loop, client, seen = build_loop(repeat, max_turns=3, context_window=1500)
    result = loop.run(base_messages())
    assert result.compress_events == 0  # 无压缩器时静默跳过

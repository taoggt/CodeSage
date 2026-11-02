"""ReAct 主循环：LLM ↔ 工具 交替执行（自研内核）。

循环骨架（约 150 行的核心）：

    while True:
        1. 估计当前上下文 token 量 → 检查预算状态（OK/SOFT/HARD/ABORT）
        2. ABORT → 熔断退出（输出已有发现）
           HARD  → 调用前强制压缩
        3. 调用 LLM（带工具 schema）
        4. 无 tool_calls → 模型认为完成，正常结束
           有 tool_calls → 逐个执行并回填结果（role=tool）
        5. 回合末：轮次/进展检查；SOFT → 回合边界压缩

设计要点：
- 循环不认识"压缩细节"与"发现收集"（依赖注入）——关注点分离；
- 工具错误不抛出，以文本回填让模型自我纠正；
- 每一步都通过 on_event 钩子可观测（CLI 进度、调试、评测 trace）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from codesage.agent.policies import StopPolicy
from codesage.agent.registry import ToolRegistry
from codesage.agent.state import AgentState
from codesage.core.cancel import CancelToken
from codesage.core.errors import LLMError
from codesage.core.models import TokenUsage
from codesage.llm.budget import BudgetState, TokenBudget
from codesage.llm.client import (
    assistant_message_with_tool_calls,
    tool_result_message,
)
from codesage.llm.tokenizer import TokenEstimator
from codesage.memory.working import WorkingMemory

logger = logging.getLogger(__name__)

# 压缩器：接收"中间区消息列表"，返回结构化摘要文本（由外部注入 LLM 实现）
Compressor = Callable[[list[dict[str, Any]]], str]
# 事件钩子：(事件名, 载荷)
EventHook = Callable[[str, dict[str, Any]], None]


@dataclass
class LoopConfig:
    max_turns: int = 12
    max_tool_result_chars: int = 6000


@dataclass
class AgentResult:
    """循环执行结果。"""

    status: str = "finished"  # finished / stopped / aborted / error
    stop_reason: str = ""
    turns: int = 0
    final_message: str = ""
    usage: TokenUsage = field(default_factory=TokenUsage)
    tool_calls_total: int = 0
    compress_events: int = 0


class AgentLoop:
    """自研 ReAct 循环。"""

    def __init__(
        self,
        *,
        client: Any,  # LLMClient 或兼容对象（Mock 亦可）
        registry: ToolRegistry,
        memory: WorkingMemory,
        budget: TokenBudget,
        estimator: TokenEstimator,
        model: str,
        policy: StopPolicy | None = None,
        config: LoopConfig | None = None,
        compressor: Compressor | None = None,
        findings_counter: Callable[[], int] | None = None,
        on_event: EventHook | None = None,
        cancel_token: CancelToken | None = None,
    ) -> None:
        self._client = client
        self._registry = registry
        self._memory = memory
        self._budget = budget
        self._estimator = estimator
        self._model = model
        self._policy = policy or StopPolicy()
        self._config = config or LoopConfig()
        self._compressor = compressor
        self._findings_counter = findings_counter
        self._on_event = on_event
        self._cancel_token = cancel_token
        self._compress_count = 0

    def _cancelled(self) -> bool:
        return self._cancel_token is not None and self._cancel_token.cancelled

    # ------------------------------------------------------------ 对外接口

    def run(self, initial_messages: list[dict[str, Any]]) -> AgentResult:
        """执行循环直到结束。

        initial_messages 会被冻结（系统提示 + 任务简报），循环内不再改动。
        """
        self._memory.freeze(initial_messages)
        state = AgentState()
        result = AgentResult()
        self._emit("start", {"tools": self._registry.names()})

        while True:
            # ---- 0. 取消检查（协作式：在每轮与每次外部调用前生效）
            if self._cancelled():
                result.status = "cancelled"
                result.stop_reason = "用户取消"
                self._emit("cancelled", {"turn": state.turn, "phase": "turn_start"})
                break

            # ---- 1. 预算检查（调用前）
            context_tokens = self._estimate_context()
            budget_state = self._budget.state(context_tokens)
            self._emit(
                "context",
                {
                    "turn": state.turn,
                    "tokens": context_tokens,
                    "ratio": round(self._budget.ratio(context_tokens), 3),
                    "state": budget_state.value,
                    "messages": len(self._memory.build()),
                },
            )

            if budget_state == BudgetState.ABORT:
                result.status = "aborted"
                result.stop_reason = (
                    f"Token 预算熔断（使用率 ≥ {int(self._budget_ratio_abort * 100)}%），"
                    "输出已有发现"
                )
                break
            if budget_state == BudgetState.HARD:
                self._compress(context_tokens)

            # ---- 2. LLM 调用
            if self._cancelled():
                result.status = "cancelled"
                result.stop_reason = "用户取消"
                self._emit("cancelled", {"turn": state.turn, "phase": "before_chat"})
                break
            try:
                response = self._client.chat(
                    self._memory.build(),
                    tools=self._registry.schemas(),
                    model=self._model,
                )
            except LLMError as exc:
                result.status = "error"
                result.stop_reason = f"LLM 调用失败：{exc}"
                break

            self._budget.record(response.usage)

            # 模型的自然语言旁白（工具调用的"理由"），供 GUI 展示思考流
            narration = (response.content or "").strip()
            if narration:
                self._emit(
                    "assistant_message",
                    {
                        "turn": state.turn,
                        "has_tool_calls": response.has_tool_calls,
                        "preview": narration[:300],
                    },
                )

            # ---- 3a. 无工具调用 = 模型认为任务完成
            if not response.has_tool_calls:
                self._memory.append(
                    {"role": "assistant", "content": response.content or ""}
                )
                result.status = "finished"
                result.stop_reason = "模型主动结束"
                result.final_message = response.content or ""
                self._emit("finish", {"message": response.content[:200]})
                break

            # ---- 3b. 有工具调用：回填 assistant 消息 → 逐个执行工具
            self._memory.append(assistant_message_with_tool_calls(response))
            call_names = [tc.name for tc in response.tool_calls]
            self._emit(
                "tool_calls",
                {
                    "turn": state.turn + 1,
                    "names": call_names,
                    # 参数摘要供前端显示"Agent 在看哪个文件"，不传完整参数（体积可控）
                    "args": [
                        {
                            "name": tc.name,
                            "preview": str(tc.arguments)[:160].replace("\n", " "),
                        }
                        for tc in response.tool_calls
                    ],
                },
            )

            cancelled_in_tools = False
            for tc in response.tool_calls:
                if self._cancelled():
                    cancelled_in_tools = True
                    break
                output = self._registry.execute(
                    tc.name,
                    tc.arguments,
                    max_result_chars=self._config.max_tool_result_chars,
                )
                self._memory.append(tool_result_message(tc.id, output))
                state.tool_calls_total += 1
                self._emit(
                    "tool_result",
                    {
                        "name": tc.name,
                        "preview": output[:120].replace("\n", " "),
                        "chars": len(output),
                    },
                )

            if cancelled_in_tools:
                result.status = "cancelled"
                result.stop_reason = "用户取消"
                self._emit("cancelled", {"turn": state.turn + 1, "phase": "tool_loop"})
                break

            # ---- 4. 回合末：进展记录 + 停止检查 + 软阈值压缩
            state.turn += 1
            if self._findings_counter is not None:
                state.findings_history.append(self._findings_counter())

            should_stop, reason = self._policy.check(state)
            if should_stop:
                result.status = "stopped"
                result.stop_reason = reason
                break

            after_tokens = self._estimate_context()
            if self._budget.state(after_tokens) in (BudgetState.SOFT, BudgetState.HARD):
                self._compress(after_tokens)

        # ---- 收尾
        result.turns = state.turn
        result.tool_calls_total = state.tool_calls_total
        result.usage = self._budget.usage
        result.compress_events = self._compress_count
        self._emit("end", {"status": result.status, "reason": result.stop_reason})
        return result

    # ------------------------------------------------------------ 内部方法

    @property
    def _budget_ratio_abort(self) -> float:
        return self._budget.abort_ratio

    def _estimate_context(self) -> int:
        return self._estimator.estimate_messages(self._memory.build())

    def _compress(self, context_tokens: int | None = None) -> None:
        """执行一次压缩（无压缩器注入时为空操作）。"""
        if self._compressor is None:
            return
        event = self._memory.compress(self._compressor)
        if event is not None:
            self._compress_count += 1
            self._emit(
                "compress",
                {
                    "removed": event.removed_messages,
                    "kept": event.kept_messages,
                    "context_tokens": context_tokens,
                },
            )

    def _emit(self, event: str, payload: dict[str, Any]) -> None:
        if self._on_event is not None:
            try:
                self._on_event(event, payload)
            except Exception:  # noqa: BLE001 - 事件钩子异常不影响主循环
                logger.exception("事件钩子执行失败：%s", event)

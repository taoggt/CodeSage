"""ReAct 主循环：LLM ↔ 工具 交替执行（自研内核）。

循环骨架：

    while True:
        1. 估计当前上下文 token 量 → 检查预算状态（OK/SOFT/HARD/ABORT）
        2. ABORT → 熔断退出（输出已有发现）
           HARD  → 调用前强制压缩
        3. 调用 LLM（带工具 schema）；失败重试一次，仍失败则降级收尾
        4. 无 tool_calls → 模型认为完成，正常结束
           有 tool_calls → 逐个执行并回填结果（role=tool）
        5. 回合末：轮次/进展检查；SOFT → 回合边界压缩

设计要点：
- 循环不认识「压缩细节」「发现收集」「工作区」——全部依赖注入；
- 工具错误不抛出，以文本回填让模型自我纠正；连续错误会额外注入纠偏提示，
  避免模型把失败结果当成事实继续推理；
- 每一步都通过 on_event 钩子可观测（CLI 进度、调试、评测 trace）。

轮次上限的**唯一真相源是 StopPolicy.max_turns**。LoopConfig 不再持有
max_turns（旧版有但从未被读取，导致「配置说是 12、实际判停用另一份」）。
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

# 压缩器：接收压缩 prompt，返回摘要文本（由外部注入 LLM 实现）
Compressor = Callable[[str], str]
# 事件钩子：(事件名, 载荷)
EventHook = Callable[[str, dict[str, Any]], None]
# 钉住块提供者：返回可由确定性状态重建的事实清单（已提交发现 / 已读文件）
PinnedState = Callable[[], str]


@dataclass
class LoopConfig:
    """循环的非策略性参数（轮次属于 StopPolicy，不放这里）。"""

    max_tool_result_chars: int = 6000
    max_llm_retries: int = 1  # 单次 chat 失败后重试几次（SDK 另自带 2 次）


@dataclass
class AgentResult:
    """循环执行结果。"""

    status: str = "finished"  # finished / stopped / aborted / degraded / cancelled / error
    stop_reason: str = ""
    turns: int = 0
    final_message: str = ""
    usage: TokenUsage = field(default_factory=TokenUsage)
    tool_calls_total: int = 0
    compress_events: int = 0
    llm_failures: int = 0


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
        pinned_state: PinnedState | None = None,
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
        self._pinned_state = pinned_state
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
        llm_failures = 0
        consecutive_tool_errors = 0
        warned_about_errors = False
        self._emit("start", {"tools": self._registry.names(), "max_turns": self._policy.max_turns})

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
                    f"Token 预算熔断（使用率 ≥ {int(self._budget.abort_ratio * 100)}%），"
                    "输出已有发现"
                )
                break
            if budget_state == BudgetState.HARD:
                self._compress(context_tokens)
                if self._budget.state(self._estimate_context()) == BudgetState.HARD:
                    # 压缩没能把预算降下来（常见原因：活跃区不足 K 轮，
                    # 或撑爆的是不可压缩的冻结区）。不假装已解决，显式上报。
                    self._emit(
                        "compress_ineffective",
                        {"turn": state.turn, "tokens": self._estimate_context()},
                    )

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
                llm_failures += 1
                self._emit(
                    "llm_error",
                    {"turn": state.turn, "attempt": llm_failures, "error": str(exc)},
                )
                if llm_failures <= self._config.max_llm_retries:
                    # 消息序列未改动，重试是幂等的；不增加 turn。
                    continue
                result.status = "degraded"
                result.stop_reason = (
                    f"LLM 连续 {llm_failures} 次调用失败，已输出此前提交的发现"
                    f"（降级完成）：{exc}"
                )
                self._emit("degraded", {"turn": state.turn, "reason": result.stop_reason})
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
            turn_errors = 0
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
                if output.startswith("ERROR"):
                    turn_errors += 1
                self._emit(
                    "tool_result",
                    {
                        "name": tc.name,
                        "preview": output[:120].replace("\n", " "),
                        "chars": len(output),
                        "is_error": output.startswith("ERROR"),
                    },
                )

            if cancelled_in_tools:
                result.status = "cancelled"
                result.stop_reason = "用户取消"
                self._emit("cancelled", {"turn": state.turn + 1, "phase": "tool_loop"})
                break

            # 连续错误纠偏：模型看到的是 ERROR 文本，但它仍可能把失败结果
            # 当成事实继续推理。连续两轮出错时显式打断一次。
            consecutive_tool_errors = (
                consecutive_tool_errors + 1 if turn_errors else 0
            )
            if consecutive_tool_errors >= 2 and not warned_about_errors:
                self._memory.append(
                    {
                        "role": "system",
                        "content": (
                            "注意：最近连续多轮工具调用返回 ERROR。不要基于失败结果"
                            "继续推理或提交发现；请先用 read_file 确认路径与行号，"
                            "再决定下一步。"
                        ),
                    }
                )
                warned_about_errors = True
                self._emit("tool_error_streak", {"streak": consecutive_tool_errors})
            if turn_errors == 0:
                warned_about_errors = False

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
        result.llm_failures = llm_failures
        self._emit("end", {"status": result.status, "reason": result.stop_reason})
        return result

    # ------------------------------------------------------------ 内部方法

    def _estimate_context(self) -> int:
        return self._estimator.estimate_messages(self._memory.build())

    def _compress(self, context_tokens: int | None = None) -> None:
        """执行一次压缩（无压缩器注入时为空操作）。

        钉住块由外部按确定性状态重建，不经过 LLM；只有「推理残留」
        才交给压缩器，避免摘要弄丢已提交发现。
        """
        if self._compressor is None:
            return
        pinned = self._pinned_state() if self._pinned_state is not None else ""
        event = self._memory.compress(self._compressor, pinned=pinned)
        if event is not None:
            self._compress_count += 1
            self._emit(
                "compress",
                {
                    "removed": event.removed_messages,
                    "kept": event.kept_messages,
                    "context_tokens": context_tokens,
                    "pinned_chars": len(pinned),
                },
            )
        elif self._memory.last_compress_reason not in ("", "no_need"):
            self._emit(
                "compress_skipped",
                {
                    "reason": self._memory.last_compress_reason,
                    "context_tokens": context_tokens,
                },
            )

    def _emit(self, event: str, payload: dict[str, Any]) -> None:
        if self._on_event is not None:
            try:
                self._on_event(event, payload)
            except Exception:  # noqa: BLE001 - 事件钩子异常不影响主循环
                logger.exception("事件钩子执行失败：%s", event)

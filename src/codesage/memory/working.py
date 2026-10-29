"""三区工作记忆 + 双阈值压缩（对齐工业界已验证的设计）。

三区模型：
    ┌────────────────────────────────────────────┐
    │ 冻结区 frozen：系统提示 + 任务简报            │  永不压缩
    │ （跨轮次完全不变 → 可命中 prompt 缓存）        │
    ├────────────────────────────────────────────┤
    │ 压缩区 summary：中间轮次的结构化摘要           │  由 LLM 压缩产生
    │ （保留：已提交发现 / 审查进度 / 未决疑点）      │
    ├────────────────────────────────────────────┤
    │ 活跃区 active：最近 K 轮原文                  │  保持原样
    │ （保证推理连续性）                            │
    └────────────────────────────────────────────┘

双阈值触发（由 AgentLoop 驱动，本模块只负责执行压缩）：
    ≥60%（soft）：回合边界压缩 —— 不打断当前推理
    ≥80%（hard）：调用前强制压缩 —— 保证下一轮放得下
    ≥95%（abort）：熔断（在 loop 中处理）

压缩质量控制：压缩器必须保留"所有已提交发现"，压缩 prompt 中有
明确约束；压缩失败（返回空）时不改动状态（宁可占 token，不可丢信息）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

Compressor = Callable[[str], str]  # 输入序列化文本，返回摘要文本


@dataclass
class CompressEvent:
    """一次压缩的记录（用于可观测性与评测统计）。"""

    removed_messages: int
    kept_messages: int
    summary_chars: int


COMPRESS_PROMPT = """你是代码审查过程的信息压缩器。把下面的审查过程压缩为结构化摘要。

压缩要求（严格执行）：
1. 必须完整保留：所有已提交的发现（编号 + 文件:行号 + 一句话结论）、
   已审查的文件清单、未决疑点与待办。
2. 丢弃：重复的文件内容、冗长的工具原始输出、已完成的分析细节。
3. 输出为简洁的要点列表（纯文本），不要任何寒暄或说明。
4. 严禁遗漏“已提交的发现”——它们是最终报告的核心数据。

{previous}## 审查过程原文

{payload}
"""


@dataclass
class WorkingMemory:
    """三区工作记忆。"""

    active_window_turns: int = 4
    _frozen: list[dict[str, Any]] = field(default_factory=list, repr=False)
    _active: list[dict[str, Any]] = field(default_factory=list, repr=False)
    _summary: str = ""
    _events: list[CompressEvent] = field(default_factory=list, repr=False)

    # ------------------------------------------------------------ 基础操作

    def freeze(self, messages: list[dict[str, Any]]) -> None:
        """设置冻结区（通常在循环开始时调用一次）。"""
        self._frozen = [dict(m) for m in messages]

    def append(self, message: dict[str, Any]) -> None:
        self._active.append(message)

    def build(self) -> list[dict[str, Any]]:
        """组装完整消息序列：冻结区 + 摘要 + 活跃区。"""
        out: list[dict[str, Any]] = list(self._frozen)
        if self._summary:
            out.append(
                {
                    "role": "system",
                    "content": (
                        "## 历史审查过程摘要（此前轮次已压缩，发现均已记录）\n"
                        f"{self._summary}"
                    ),
                }
            )
        out.extend(self._active)
        return out

    # ------------------------------------------------------------ 状态查询

    @property
    def summary(self) -> str:
        return self._summary

    @property
    def events(self) -> list[CompressEvent]:
        return list(self._events)

    @property
    def compressed(self) -> bool:
        return bool(self._summary)

    @property
    def active_count(self) -> int:
        return len(self._active)

    # ------------------------------------------------------------ 压缩

    def compress(self, compressor: Compressor) -> CompressEvent | None:
        """压缩中间区（活跃区中除最近 K 轮外的部分）。

        返回 None 表示：无需压缩（轮数不足）或压缩失败（保持原状）。
        """
        keep_from = self._find_keep_index()
        if keep_from <= 0:
            return None  # 活跃区不足 K 轮，不压缩

        middle = self._active[:keep_from]
        payload = self._serialize(middle)
        if not payload.strip():
            return None

        prompt = COMPRESS_PROMPT.format(
            previous=(
                f"## 已有摘要（与本次新内容合并，不可丢失其中信息）\n{self._summary}\n\n"
                if self._summary
                else ""
            ),
            payload=payload,
        )

        try:
            new_summary = compressor(prompt)
        except Exception:  # noqa: BLE001 - 压缩失败保持原状（信息优先于成本）
            return None

        if not new_summary or not new_summary.strip():
            return None

        event = CompressEvent(
            removed_messages=len(middle),
            kept_messages=len(self._active) - keep_from,
            summary_chars=len(new_summary),
        )
        self._summary = new_summary.strip()
        self._active = self._active[keep_from:]
        self._events.append(event)
        return event

    # ------------------------------------------------------------ 内部

    def _find_keep_index(self) -> int:
        """找到活跃区应保留的起始下标（保留最近 K 轮完整）。

        轮边界定义：assistant 消息开始一轮，其后跟着该轮的 tool 结果。
        cut 点必须落在某个 assistant 处——保证不切断 assistant-tool 配对
        （配对断了会造成 OpenAI 协议错误：tool 消息找不到对应的 tool_call）。
        返回 0 表示当前轮数不足、无需压缩。
        """
        assistant_indices = [
            idx
            for idx, msg in enumerate(self._active)
            if msg.get("role") == "assistant"
        ]
        if len(assistant_indices) <= self.active_window_turns:
            return 0
        return assistant_indices[-self.active_window_turns]

    @staticmethod
    def _serialize(messages: list[dict[str, Any]]) -> str:
        """把中间区消息序列化为压缩器输入（控制单条长度，防止二次膨胀）。"""
        parts: list[str] = []
        for msg in messages:
            role = msg.get("role", "?")
            content = msg.get("content") or ""
            if role == "assistant":
                tool_calls = msg.get("tool_calls") or []
                names = [
                    (tc.get("function") or {}).get("name", "?")
                    for tc in tool_calls
                    if isinstance(tc, dict)
                ]
                head = f"[assistant] 调用工具：{', '.join(names)}" if names else "[assistant]"
                parts.append(f"{head}\n{str(content)[:600]}")
            elif role == "tool":
                parts.append(f"[tool 结果] {str(content)[:900]}")
            else:
                parts.append(f"[{role}] {str(content)[:600]}")
        return "\n---\n".join(parts)

"""dismiss_candidate 工具：驳回候选发现（LLM 复核结论的落地通道）。

为什么需要它：
- AST 规则的候选发现里必然有误报（如"未使用 with"但实际已手动 close）；
- 如果模型只是"不重新提交"，原规则发现仍会进入报告——复核形同虚设；
- 因此提供显式驳回通道：被驳回的候选在合并阶段被过滤掉，
  驳回记录写入报告 metadata（可观测、可审计）。
"""

from __future__ import annotations

from typing import Callable

from codesage.agent.registry import tool


def make_dismiss_candidate_tool(
    dismissed: list[dict],
) -> Callable[..., str]:
    """创建 dismiss_candidate 工具，驳回记录写入 dismissed 列表。"""

    @tool(name="dismiss_candidate")
    def dismiss_candidate(file: str, line_start: int, reason: str = "") -> str:
        """驳回一条候选发现（复核后判定为误报，不进最终报告）。

        复核候选发现时：判定为误报 → 调用本工具驳回；判定属实 →
        调用 submit_finding 重新提交（附修复建议）。

        Args:
            file: 候选发现所在文件（相对路径）
            line_start: 候选发现的行号（以候选清单中的行号为准）
            reason: 驳回理由（一句话说明为什么是误报）
        """
        try:
            line = int(line_start)
        except (TypeError, ValueError):
            return "ERROR: line_start 必须是整数"
        file_text = str(file).strip().lstrip("./")
        record = {
            "file": file_text,
            "line": max(1, line),
            "reason": str(reason).strip(),
        }
        dismissed.append(record)
        return (
            f"已驳回候选：{file_text}:{line}"
            + (f"（理由：{record['reason']}）" if record["reason"] else "")
            + f"。累计驳回 {len(dismissed)} 条。"
        )

    return dismiss_candidate

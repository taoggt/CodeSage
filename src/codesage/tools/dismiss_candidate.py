"""dismiss_candidate 工具：驳回候选发现（LLM 复核结论的落地通道）。

为什么需要它：
- AST 规则的候选发现里必然有误报（如"未使用 with"但实际已手动 close）；
- 如果模型只是"不重新提交"，原规则发现仍会进入报告——复核形同虚设；
- 因此提供显式驳回通道：被驳回的候选在合并阶段被过滤掉，
  驳回记录写入报告 metadata（可观测、可审计）。

审计后补上的约束：驳回是**破坏性操作**（会从最终报告里删掉一条真实发现），
所以不能让模型按任意 file:line 驳回。现在要求：
1. 路径必须在工作区内且文件真实存在、行号不越界；
2. 该位置附近必须确实存在一条候选发现（否则驳回无效）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from codesage.agent.registry import tool
from codesage.core.models import NEAR_LINE_WINDOW, Finding
from codesage.tools.paths import normalize_relative, resolve_inside


def make_dismiss_candidate_tool(
    dismissed: list[dict],
    workspace: Path | None = None,
    candidates: list[Finding] | None = None,
) -> Callable[..., str]:
    """创建 dismiss_candidate 工具，驳回记录写入 dismissed 列表。

    workspace / candidates 为可选依赖注入：不传时维持旧的宽松行为
    （便于单元测试），pipeline 组装时两者都会传入，启用严格校验。
    """
    root = Path(workspace).resolve() if workspace is not None else None
    candidate_index: list[tuple[str, int]] | None = (
        [(f.file, f.line_start) for f in candidates] if candidates is not None else None
    )

    @tool(name="dismiss_candidate")
    def dismiss_candidate(file: str, line_start: int, reason: str = "") -> str:
        """驳回一条候选发现（复核后判定为误报，不进最终报告）。

        复核候选发现时：判定为误报 → 调用本工具驳回；判定属实 →
        调用 submit_finding 重新提交（附修复建议）。

        Args:
            file: 候选发现所在文件（相对路径，必须与候选清单一致）
            line_start: 候选发现的行号（以候选清单中的行号为准）
            reason: 驳回理由（一句话说明为什么是误报）
        """
        try:
            line = int(line_start)
        except (TypeError, ValueError):
            return "ERROR: line_start 必须是整数"

        raw_path = str(file).strip()
        rel = raw_path
        if root is not None:
            target = resolve_inside(root, raw_path)
            if target is None:
                return f"ERROR: 非法路径（必须是工作区内的文件）：{raw_path}"
            if not target.is_file():
                return f"ERROR: 文件不存在：{raw_path}，无法驳回"
            rel = normalize_relative(target, root)
            try:
                total = len(
                    target.read_text(encoding="utf-8", errors="replace").splitlines()
                )
            except OSError as exc:
                return f"ERROR: 读取 {rel} 失败：{exc}"
            if line < 1 or line > total:
                return f"ERROR: line_start={line} 超过 {rel} 的总行数（{total}）"
        else:
            line = max(1, line)

        if candidate_index is not None:
            matched = any(
                cf == rel and abs(cl - line) <= NEAR_LINE_WINDOW
                for cf, cl in candidate_index
            )
            if not matched:
                return (
                    f"ERROR: {rel}:{line} 附近没有候选发现，驳回无效。"
                    "请核对候选清单里的文件与行号；若该问题并非误报，"
                    "请用 submit_finding 提交。"
                )

        record = {
            "file": rel,
            "line": line,
            "reason": str(reason).strip(),
        }
        dismissed.append(record)
        return (
            f"已驳回候选：{rel}:{line}"
            + (f"（理由：{record['reason']}）" if record["reason"] else "")
            + f"。累计驳回 {len(dismissed)} 条。"
        )

    return dismiss_candidate

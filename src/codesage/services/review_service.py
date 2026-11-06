"""审查服务：把 ReviewRequest 变成一次真实的 run_review 调用。

这是 GUI 与 CLI 共享的唯一入口——路由层不直接 import pipeline，
保证"GUI 跑出来的报告和 CLI 一模一样"。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from codesage.core.cancel import CancelToken
from codesage.core.config import Settings
from codesage.pipeline import ReviewOptions, run_review
from codesage.services.workspace_service import WorkspaceRegistry

Publish = Callable[[str, dict], None]


@dataclass
class ReviewRequest:
    """一次审查请求（对应 GUI 表单 / POST /api/jobs）。"""

    target: str
    profile: str = "full"
    use_llm: bool = True
    changed_only: bool = False
    max_turns: int | None = None
    keep_partial: bool = True
    languages: str | None = None


def run(
    settings: Settings,
    request: ReviewRequest,
    registry: WorkspaceRegistry,
    cancel_token: CancelToken | None = None,
    publish: Publish | None = None,
) -> dict[str, Any]:
    """执行审查并返回 {report, review_id, summary}。"""
    target = registry.get(request.target)  # 未登记的路径直接抛 PathOutsideRoot
    if request.profile not in ("full", "security", "performance"):
        raise ValueError(f"非法 profile：{request.profile}")

    def on_event(event: str, payload: dict) -> None:
        if publish is not None:
            publish(event, payload)

    options = ReviewOptions(
        use_llm=request.use_llm,
        profile=request.profile,
        changed_only=request.changed_only,
        quiet=True,
        max_turns=request.max_turns,
        languages=request.languages,
        on_event=on_event,
        cancel_token=cancel_token,
        persist=True,
        persist_on_cancel=request.keep_partial,
    )
    report = run_review(target, settings, options)

    return {
        "report": report.to_dict(),
        "review_id": report.metadata.get("review_id"),
        "summary": {
            "target": report.target,
            "score": report.score,
            "findings": len(report.findings),
            "by_severity": report.by_severity(),
            "duration_sec": round(report.duration_sec, 2),
            "tokens": report.usage.total,
            "cost_yuan": report.metadata.get("cost_yuan", 0.0),
            "llm_enabled": report.llm_enabled,
            "turns": report.turns,
            "stop_reason": report.stop_reason,
            "cancelled": bool(report.metadata.get("cancelled")),
            "partial": bool(report.metadata.get("partial")),
        },
    }


def target_kind(path: Path) -> str:
    return "dir" if path.is_dir() else "file"

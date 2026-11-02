"""反馈服务：定位 finding、写入反馈、立即提炼单条经验。

GUI 把原来"review → 记 ID → feedback → --mine"四步压成一次点击：
提交反馈时顺带把这条反馈提炼成经验（right 除外），用户不需要理解 --mine。
"""

from __future__ import annotations

from typing import Any

from codesage.memory.store import Store
from codesage.optim.feedback import VALID_VERDICTS, FeedbackEntry, record_feedback
from codesage.optim.lessons import (
    LESSON_FALSE_NEGATIVE,
    LESSON_FALSE_POSITIVE,
    LessonBook,
)


def locate_finding(
    store: Store, finding_id: str, review_id: int | None = None
) -> tuple[dict | None, str]:
    """定位 finding 并返回其所属项目（project）。

    优先用前端传来的 review_id（只解析一份 payload）；
    没有上下文时退回"最近 50 次审查"扫描（兼容 CLI 用法）。
    """
    if review_id is not None:
        row = store.get_review_row(review_id)
        if row is None:
            return None, ""
        for finding in store.get_review_findings(review_id):
            if str(finding.get("id")) == finding_id:
                return finding, str(row.get("project") or "")
        return None, str(row.get("project") or "")

    for row in store.recent_reviews(limit=50):
        for finding in store.get_review_findings(int(row["id"])):
            if str(finding.get("id")) == finding_id:
                return finding, str(row.get("project") or "")
    return None, ""


def _mine_single(store: Store, entry: FeedbackEntry) -> int:
    """把一条反馈直接提炼成经验，返回 lesson id（已存在返回 -1）。"""
    if entry.verdict == "wrong":
        kind = LESSON_FALSE_POSITIVE
        prefix = "【误报案例】"
    elif entry.verdict == "missed":
        kind = LESSON_FALSE_NEGATIVE
        prefix = "【漏报案例】"
    else:
        return -1

    content = f"{prefix}{entry.file or '?'} {entry.title or '?'}"
    if entry.note:
        content += f"：{entry.note}"
    return LessonBook(store).add(
        kind, content, pattern=entry.category, project=entry.project
    )


def submit(
    store: Store,
    finding_id: str,
    verdict: str,
    note: str = "",
    review_id: int | None = None,
    *,
    auto_mine: bool = True,
    category: str = "",
    file: str = "",
    title: str = "",
) -> dict[str, Any]:
    """提交反馈；auto_mine=True 时同步生成经验。"""
    if verdict not in VALID_VERDICTS:
        raise ValueError(f"非法 verdict '{verdict}'，可用值：{', '.join(VALID_VERDICTS)}")

    located, project = locate_finding(store, finding_id, review_id)
    record = located or {}
    entry = FeedbackEntry(
        finding_id=finding_id,
        verdict=verdict,
        project=project,
        category=str(record.get("category") or category or ""),
        file=str(record.get("file") or file or ""),
        title=str(record.get("title") or title or ""),
        note=note,
    )
    feedback_id = record_feedback(store, entry)
    lesson_id = _mine_single(store, entry) if auto_mine else -1
    # 自动提炼后标记"已处理"，避免它继续出现在"批量提炼"的待办计数里
    if auto_mine and verdict in ("wrong", "missed"):
        store.mark_feedback_mined([feedback_id])

    return {
        "id": feedback_id,
        "finding_id": finding_id,
        "verdict": verdict,
        "project": project,
        "located": located is not None,
        "lesson_id": lesson_id,
        "lesson_created": lesson_id > 0,
        "finding": located,
    }


def list_feedback(
    store: Store,
    limit: int = 100,
    offset: int = 0,
    project: str | None = None,
    verdict: str | None = None,
) -> dict[str, Any]:
    items = store.all_feedback(limit=limit, project=project, verdict=verdict, offset=offset)
    return {
        "total": store.feedback_count(project=project),
        "limit": limit,
        "offset": offset,
        "items": items,
    }


def stats(store: Store, project: str | None = None) -> dict[str, Any]:
    counts = store.feedback_stats(project=project)
    return {
        "wrong": int(counts.get("wrong", 0)),
        "right": int(counts.get("right", 0)),
        "missed": int(counts.get("missed", 0)),
        "total": sum(int(v) for v in counts.values()),
    }

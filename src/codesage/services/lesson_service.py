"""错题本服务：经验的增删查、手动补漏报、真正会注入 Prompt 的文本预览。

"注入预览"是这一层最有价值的能力：`few_shot_block()` 才是真正拼进
system prompt 的字符串，用户能看见条数上限（6）与字符上限（1200）如何被截断，
而不是加完经验后只能猜"到底生效了没"。
"""

from __future__ import annotations

from typing import Any

from codesage.memory.store import Store
from codesage.optim.lessons import (
    LESSON_FALSE_NEGATIVE,
    LESSON_FALSE_POSITIVE,
    MAX_BLOCK_CHARS,
    LessonBook,
)

VALID_KINDS = (LESSON_FALSE_POSITIVE, LESSON_FALSE_NEGATIVE)
DEFAULT_INJECT_LIMIT = 6


def list_lessons(
    store: Store,
    kind: str | None = None,
    project: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    if kind and kind not in VALID_KINDS:
        raise ValueError(f"非法 kind '{kind}'，可用值：{', '.join(VALID_KINDS)}")
    items = store.lessons(kind=kind, limit=limit, project=project, offset=offset)
    return {
        "total": store.lesson_count(kind=kind, project=project),
        "limit": limit,
        "offset": offset,
        "items": items,
    }


def add_lesson(
    store: Store,
    kind: str,
    content: str,
    pattern: str = "",
    project: str = "",
) -> dict[str, Any]:
    if kind not in VALID_KINDS:
        raise ValueError(f"非法 kind '{kind}'，可用值：{', '.join(VALID_KINDS)}")
    if not content.strip():
        raise ValueError("经验内容不能为空")
    lesson_id = LessonBook(store).add(
        kind, content.strip(), pattern=pattern, project=project
    )
    return {"id": lesson_id, "dup": lesson_id < 0}


def add_missed(
    store: Store,
    file: str,
    line: int | str | None,
    title: str,
    category: str = "",
    note: str = "",
    project: str = "",
) -> dict[str, Any]:
    """手动补一条漏报（找不到 finding_id 的场景，语义比 -v missed 更直白）。"""
    if not title.strip():
        raise ValueError("请填写漏报的简要描述")
    location = file or "?"
    if line not in (None, "", 0, "0"):
        location = f"{location}:{line}"
    content = f"【漏报案例】{location} {title.strip()}"
    if note.strip():
        content += f"：{note.strip()}"
    lesson_id = LessonBook(store).add(
        LESSON_FALSE_NEGATIVE, content, pattern=category, project=project
    )
    return {"id": lesson_id, "dup": lesson_id < 0, "content": content}


def delete_lesson(store: Store, lesson_id: int) -> dict[str, Any]:
    return {"deleted": store.delete_lesson(lesson_id)}


def mine(store: Store, project: str | None = None) -> dict[str, Any]:
    """把"未提炼"的反馈批量转成经验（保留 CLI 的 --mine 语义）。

    只处理 mined=0 的反馈；提炼后立即标记，因此重复调用不会重复生成，
    删除经验也不会在下次调用时复活。
    """
    added = LessonBook(store).mine_from_feedback()
    return {
        "false_positive": added.get("false_positive", 0),
        "false_negative": added.get("false_negative", 0),
        "total": sum(added.values()),
        "pending": store.unmined_feedback_count(project=project),
    }


def pending(store: Store, project: str | None = None) -> int:
    """还有多少条反馈尚未提炼（wrong / missed）。"""
    return store.unmined_feedback_count(project=project)


def reset_mined(store: Store, project: str | None = None) -> dict[str, Any]:
    """清除"已提炼"标记，允许历史反馈被重新提炼（用户显式操作）。"""
    reset = store.reset_feedback_mined(project=project)
    return {"reset": reset, "pending": store.unmined_feedback_count(project=project)}


def preview(
    store: Store,
    project: str | None = None,
    limit: int = DEFAULT_INJECT_LIMIT,
) -> dict[str, Any]:
    """返回真正会注入 system prompt 的经验块（含截断信息）。"""
    book = LessonBook(store)
    block = book.few_shot_block(limit=limit, project=project)
    injected = [line for line in block.split("\n") if line.strip()] if block else []
    candidates = book.all(limit=limit, project=project)
    return {
        "block": block,
        "chars": len(block),
        "limit_chars": MAX_BLOCK_CHARS,
        "limit_items": limit,
        "injected": [{"text": line, "chars": len(line)} for line in injected],
        "candidates": len(candidates),
        "truncated_by_chars": bool(block) and len(injected) < len(candidates),
        "fits": len(block) <= MAX_BLOCK_CHARS,
    }


def stats(store: Store, project: str | None = None) -> dict[str, int]:
    return {
        "false_positive": store.lesson_count(kind=LESSON_FALSE_POSITIVE, project=project),
        "false_negative": store.lesson_count(kind=LESSON_FALSE_NEGATIVE, project=project),
    }

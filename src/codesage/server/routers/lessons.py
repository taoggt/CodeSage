"""错题本：列表、新增、手动补漏报、批量提炼、注入预览、删除。"""

from __future__ import annotations

from fastapi import APIRouter, Query

from codesage.server.deps import StoreDep
from codesage.server.schemas import LessonCreateRequest, MineRequest, MissedLessonRequest
from codesage.services import lesson_service

router = APIRouter(prefix="/lessons", tags=["lessons"])


@router.get("")
def list_lessons(
    store: StoreDep,
    kind: str | None = Query(None),
    project: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    result = lesson_service.list_lessons(
        store, kind=kind, project=project, limit=limit, offset=offset
    )
    result["stats"] = lesson_service.stats(store, project=project)
    result["pending"] = lesson_service.pending(store, project=project)
    return result


@router.get("/preview")
def preview(
    store: StoreDep,
    project: str | None = Query(None),
    limit: int = Query(6, ge=1, le=20),
) -> dict:
    return lesson_service.preview(store, project=project, limit=limit)


@router.post("")
def create(payload: LessonCreateRequest, store: StoreDep) -> dict:
    return lesson_service.add_lesson(
        store,
        kind=payload.kind,
        content=payload.content,
        pattern=payload.pattern,
        project=payload.project,
    )


@router.post("/missed")
def create_missed(payload: MissedLessonRequest, store: StoreDep) -> dict:
    return lesson_service.add_missed(
        store,
        file=payload.file,
        line=payload.line,
        title=payload.title,
        category=payload.category,
        note=payload.note,
        project=payload.project,
    )


@router.post("/mine")
def mine(payload: MineRequest, store: StoreDep) -> dict:
    return lesson_service.mine(store, project=payload.project)


@router.post("/reset-mined")
def reset_mined(payload: MineRequest, store: StoreDep) -> dict:
    """清除"已提炼"标记，让历史反馈可被重新提炼（删除经验后想恢复时用）。"""
    return lesson_service.reset_mined(store, project=payload.project)


@router.delete("/{lesson_id}")
def delete(lesson_id: int, store: StoreDep) -> dict:
    return lesson_service.delete_lesson(store, lesson_id)

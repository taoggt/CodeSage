"""反馈：提交（含自动提炼经验）、流水、统计。"""

from __future__ import annotations

from fastapi import APIRouter, Query

from codesage.server.deps import StoreDep
from codesage.server.schemas import FeedbackRequest
from codesage.services import feedback_service

router = APIRouter(prefix="/feedback", tags=["feedback"])


@router.post("")
def submit(payload: FeedbackRequest, store: StoreDep) -> dict:
    return feedback_service.submit(
        store,
        finding_id=payload.finding_id,
        verdict=payload.verdict,
        note=payload.note,
        review_id=payload.review_id,
        auto_mine=payload.auto_mine,
    )


@router.get("")
def list_feedback(
    store: StoreDep,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    project: str | None = Query(None),
    verdict: str | None = Query(None),
) -> dict:
    return feedback_service.list_feedback(
        store, limit=limit, offset=offset, project=project, verdict=verdict
    )


@router.get("/stats")
def stats(store: StoreDep, project: str | None = Query(None)) -> dict:
    return feedback_service.stats(store, project=project)

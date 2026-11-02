"""审查报告：历史分页、详情、导出、删除、两次对比。"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Query, Response

from codesage.server.deps import StoreDep
from codesage.server.errors import ApiError
from codesage.server.schemas import PurgeRequest
from codesage.services import export_service, history_service

router = APIRouter(prefix="/reviews", tags=["reviews"])


@router.get("")
def list_reviews(
    store: StoreDep,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    project: str | None = Query(None),
    q: str | None = Query(None, max_length=200),
) -> dict:
    return history_service.list_reviews(
        store, limit=limit, offset=offset, project=project, query=q
    )


@router.get("/compare")
def compare(
    store: StoreDep,
    a: int = Query(..., ge=1),
    b: int = Query(..., ge=1),
) -> dict:
    try:
        return history_service.compare_reviews(store, a, b)
    except ValueError as exc:
        raise ApiError(404, "review_not_found", str(exc)) from exc


@router.post("/purge")
def purge(store: StoreDep, payload: PurgeRequest) -> dict:
    """清理评测误写入的临时记录（target 含 codesage_eval_）。"""
    return {"deleted": history_service.purge_eval_rows(store), "kind": payload.kind}


@router.get("/{review_id}")
def detail(review_id: int, store: StoreDep) -> dict:
    payload = history_service.get_detail(store, review_id)
    if payload is None:
        raise ApiError(404, "review_not_found", f"找不到记录 #{review_id}")
    return payload


@router.get("/{review_id}/export")
def export(review_id: int, store: StoreDep, format: str = Query("md")) -> Response:
    payload = store.get_review_payload(review_id)
    if payload is None:
        raise ApiError(404, "review_not_found", f"找不到记录 #{review_id}")
    body, filename, media_type = export_service.render(payload, format)
    return Response(
        content=body,
        media_type=media_type,
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{filename}\"; "
                f"filename*=UTF-8''{quote(filename)}"
            )
        },
    )


@router.delete("/{review_id}")
def delete(review_id: int, store: StoreDep) -> dict:
    deleted = history_service.delete_review(store, review_id)
    if not deleted:
        raise ApiError(404, "review_not_found", f"找不到记录 #{review_id}")
    return {"deleted": deleted}

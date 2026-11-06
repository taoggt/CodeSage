"""任务：创建审查任务、快照、SSE 事件流、取消。"""

from __future__ import annotations

from fastapi import APIRouter, Header, Query
from fastapi.responses import StreamingResponse

from codesage.server.deps import JobsDep, RegistryDep
from codesage.server.errors import ApiError
from codesage.server.schemas import ReviewJobRequest
from codesage.server.sse import stream_job_events
from codesage.services.review_service import ReviewRequest

router = APIRouter(prefix="/jobs", tags=["jobs"])

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


@router.post("", status_code=202)
def create_job(
    payload: ReviewJobRequest,
    registry: RegistryDep,
    jobs: JobsDep,
) -> dict:
    # 先做路径校验：未登记 / 不存在直接 4xx，不必等任务失败
    target = registry.get(payload.target)
    if not target.exists():
        raise ApiError(404, "invalid_target", f"路径不存在：{payload.target}")

    request = ReviewRequest(
        target=str(target),
        profile=payload.profile,
        use_llm=payload.use_llm,
        changed_only=payload.changed_only,
        max_turns=payload.max_turns,
        keep_partial=payload.keep_partial,
        languages=payload.languages,
    )
    job = jobs.submit_review(request, registry)
    return {"job_id": job.id, "status": job.status}


@router.get("")
def list_jobs(jobs: JobsDep, limit: int = Query(20, ge=1, le=50)) -> dict:
    return {"items": [jobs.snapshot(job) for job in jobs.list_jobs(limit)]}


@router.get("/{job_id}")
def get_job(job_id: str, jobs: JobsDep) -> dict:
    job = jobs.get(job_id)
    if job is None:
        raise ApiError(404, "job_not_found", f"任务不存在：{job_id}")
    return jobs.snapshot(job)


@router.get("/{job_id}/events")
def job_events(
    job_id: str,
    jobs: JobsDep,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
):
    job = jobs.get(job_id)
    if job is None:
        raise ApiError(404, "job_not_found", f"任务不存在：{job_id}")
    try:
        cursor = int(last_event_id or 0)
    except ValueError:
        cursor = 0
    return StreamingResponse(
        stream_job_events(job, jobs, cursor),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str, jobs: JobsDep) -> dict:
    job = jobs.get(job_id)
    if job is None:
        raise ApiError(404, "job_not_found", f"任务不存在：{job_id}")
    if job.terminal:
        raise ApiError(409, "job_finished", f"任务已结束（{job.status}），无法取消")
    jobs.cancel(job_id)
    return {"status": job.status, "message": "已请求取消（在途 LLM 调用返回后生效）"}

"""评测：数据集发现、后台运行（走同一 SSE）、结果、A/B 对比。"""

from __future__ import annotations

from fastapi import APIRouter, Query

from codesage.server.deps import JobsDep
from codesage.server.errors import ApiError
from codesage.server.schemas import EvalJobRequest
from codesage.services import eval_service

router = APIRouter(prefix="/eval", tags=["eval"])


@router.get("/datasets")
def datasets() -> dict:
    return {"items": eval_service.discover_datasets()}


@router.get("/results")
def results(results_dir: str = Query(".codesage-eval")) -> dict:
    return {"items": eval_service.list_results(results_dir=results_dir)}


@router.get("/compare")
def compare(
    a: str = Query(...),
    b: str = Query(...),
    results_dir: str = Query(".codesage-eval"),
) -> dict:
    try:
        return eval_service.compare(a, b, results_dir=results_dir)
    except Exception as exc:  # noqa: BLE001 - HarnessError 统一转 400
        raise ApiError(400, "eval_compare_failed", str(exc)) from exc


@router.get("/results/{tag}")
def result_detail(
    tag: str, results_dir: str = Query(".codesage-eval")
) -> dict:
    data = eval_service.get_result(tag, results_dir=results_dir)
    if data is None:
        raise ApiError(404, "eval_result_not_found", f"找不到评测结果：{tag}")
    return data


@router.post("", status_code=202)
def start(payload: EvalJobRequest, jobs: JobsDep) -> dict:
    job = jobs.submit_eval(
        {
            "dataset": payload.dataset,
            "tag": payload.tag,
            "mock": payload.mock,
            "sample": payload.sample,
            "profile": payload.profile,
            "results_dir": payload.results_dir,
        }
    )
    return {"job_id": job.id, "status": job.status, "kind": "eval"}

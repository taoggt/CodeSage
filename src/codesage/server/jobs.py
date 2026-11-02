"""进程内任务管理：审查 / 评测的后台执行与事件扇出。

为什么必须单进程单 worker：
- Job、事件缓冲、CancelToken、线程池都是进程内对象；
- uvicorn 多 worker 会让 POST /api/jobs 与 GET /api/jobs/{id}/events 落到不同进程，
  直接 404。当前规模不值得引入 Redis/外部队列，所以写死单进程。

并发模型：
- 长任务（LLM 最长 120s）跑在 ThreadPoolExecutor，绝不占用事件循环；
- 事件先入 deque（序列号单调递增，兼作 SSE 的 id），再扇出到每个订阅者的 asyncio.Queue；
- 线程 → 事件循环用 loop.call_soon_threadsafe，慢客户端丢最旧事件，不反压任务线程。
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

from codesage.core.cancel import CancelToken
from codesage.core.config import Settings
from codesage.services.eval_service import run as run_eval
from codesage.services.review_service import ReviewRequest
from codesage.services.review_service import run as run_review_job
from codesage.services.workspace_service import WorkspaceRegistry

TERMINAL_STATUSES = {"done", "failed", "cancelled"}

# 事件 → 阶段映射（前端阶段时间线直接消费 job.phase）
_PHASE_BY_EVENT = {
    "scan": "scan",
    "static_done": "static",
    "start": "llm",
    "eval_start": "eval",
}


@dataclass
class JobEvent:
    seq: int
    type: str
    payload: dict
    ts: float

    def to_sse(self) -> str:
        data = json.dumps(
            {"seq": self.seq, "ts": round(self.ts, 3), **self.payload},
            ensure_ascii=False,
            default=str,
        )
        return f"id: {self.seq}\nevent: {self.type}\ndata: {data}\n\n"


@dataclass
class Job:
    id: str
    kind: str
    params: dict
    workspace: str
    status: str = "queued"
    phase: str = ""
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    events: deque = field(default_factory=lambda: deque(maxlen=2000))
    dropped: int = 0
    last_seq: int = 0
    subscribers: set[asyncio.Queue] = field(default_factory=set)
    cancel: CancelToken = field(default_factory=CancelToken)
    result: dict | None = None
    error: dict | None = None
    counters: dict = field(default_factory=dict)
    lock: threading.RLock = field(default_factory=threading.RLock)
    loop: asyncio.AbstractEventLoop | None = None

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def elapsed(self) -> float:
        end = self.finished_at or time.time()
        return round(end - self.created_at, 1)


class JobBusy(RuntimeError):
    """同类任务并发超限。"""

    def __init__(self, retry_after: int = 5) -> None:
        super().__init__("同类任务并发超限，请稍后重试")
        self.retry_after = retry_after


class JobManager:
    """任务注册表 + 执行器 + 事件总线。"""

    def __init__(self, settings: Settings) -> None:
        from concurrent.futures import ThreadPoolExecutor

        self._settings = settings
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()
        self._review_pool = ThreadPoolExecutor(
            max_workers=max(1, settings.server.max_review_jobs),
            thread_name_prefix="cs-review",
        )
        self._eval_pool = ThreadPoolExecutor(
            max_workers=max(1, settings.server.max_eval_jobs),
            thread_name_prefix="cs-eval",
        )

    # ------------------------------------------------------------ 提交

    def submit_review(self, request: ReviewRequest, registry: WorkspaceRegistry) -> Job:
        self._assert_capacity("review", self._settings.server.max_review_jobs)
        job = self._create_job(
            kind="review",
            params={
                "target": request.target,
                "profile": request.profile,
                "use_llm": request.use_llm,
                "changed_only": request.changed_only,
                "max_turns": request.max_turns,
                "keep_partial": request.keep_partial,
            },
            workspace=request.target,
        )
        self._review_pool.submit(self._run_review, job, request, registry)
        return job

    def submit_eval(self, params: dict) -> Job:
        self._assert_capacity("eval", self._settings.server.max_eval_jobs)
        job = self._create_job(
            kind="eval",
            params=params,
            workspace=str(params.get("dataset") or ""),
        )
        self._eval_pool.submit(self._run_eval, job, params)
        return job

    def _create_job(self, kind: str, params: dict, workspace: str) -> Job:
        job = Job(
            id=uuid.uuid4().hex[:12],
            kind=kind,
            params=params,
            workspace=workspace,
            events=deque(maxlen=self._settings.server.event_buffer),
        )
        try:
            job.loop = asyncio.get_running_loop()
        except RuntimeError:
            job.loop = None  # 同步测试环境
        with self._lock:
            self._jobs[job.id] = job
            self._reap_locked()
        self.publish(job, "queued", {"kind": kind, "params": params})
        return job

    def _assert_capacity(self, kind: str, limit: int) -> None:
        with self._lock:
            running = sum(
                1
                for job in self._jobs.values()
                if job.kind == kind and job.status in ("queued", "running", "cancelling")
            )
        if running >= limit:
            raise JobBusy()

    # ------------------------------------------------------------ 执行

    def _run_review(
        self, job: Job, request: ReviewRequest, registry: WorkspaceRegistry
    ) -> None:
        self._start(job)
        try:
            result = run_review_job(
                self._settings,
                request,
                registry,
                cancel_token=job.cancel,
                publish=lambda event, payload: self.publish(job, event, payload),
            )
            summary = result.get("summary") or {}
            status = "cancelled" if summary.get("cancelled") else "done"
            self._finish(job, status, result=result)
        except Exception as exc:  # noqa: BLE001 - 任何异常都要落成终态
            self._finish(
                job,
                "failed",
                error={"code": "review_failed", "message": str(exc)},
            )

    def _run_eval(self, job: Job, params: dict) -> None:
        self._start(job)
        try:
            summary = run_eval(
                self._settings,
                dataset=params["dataset"],
                tag=params["tag"],
                mock=bool(params.get("mock")),
                sample=params.get("sample"),
                profile=str(params.get("profile") or "full"),
                results_dir=params.get("results_dir") or ".codesage-eval",  # type: ignore[arg-type]
                cancel_token=job.cancel,
                on_phase=lambda event, payload: self.publish(job, event, payload),
            )
            status = "cancelled" if summary.get("cancelled") else "done"
            self._finish(job, status, result={"eval": summary})
        except Exception as exc:  # noqa: BLE001
            self._finish(
                job, "failed", error={"code": "eval_failed", "message": str(exc)}
            )

    def _start(self, job: Job) -> None:
        with job.lock:
            job.status = "running"
            job.started_at = time.time()
            if job.cancel.cancelled:
                job.status = "cancelling"
        self.publish(job, "started", {"kind": job.kind})

    def _finish(
        self,
        job: Job,
        status: str,
        result: dict | None = None,
        error: dict | None = None,
    ) -> None:
        with job.lock:
            job.status = status
            job.finished_at = time.time()
            job.result = result
            job.error = error
        payload: dict[str, Any] = {
            "status": status,
            "kind": job.kind,
            "duration": job.elapsed(),
        }
        if result is not None:
            if job.kind == "eval":
                summary = result.get("eval") or {}
                # 只带摘要字段：逐案例明细由 GET /api/eval/results/{tag} 单独拉
                payload.update(
                    {
                        "tag": summary.get("tag"),
                        "recall": summary.get("recall"),
                        "precision": summary.get("precision"),
                        "f1": summary.get("f1"),
                        "cases": summary.get("cases"),
                        "cancelled": bool(summary.get("cancelled")),
                    }
                )
            else:
                summary = result.get("summary") or {}
                payload.update(
                    {
                        "review_id": result.get("review_id"),
                        "score": summary.get("score"),
                        "findings": summary.get("findings"),
                    }
                )
        if error is not None:
            payload["error"] = error
        self.publish(job, "end", payload)

        sentinel: Any = None
        with job.lock:
            queues = list(job.subscribers)
            job.subscribers.clear()
        for queue in queues:
            self._put(queue, sentinel, job.loop)

    # ------------------------------------------------------------ 事件总线

    def publish(self, job: Job, event: str, payload: dict) -> None:
        with job.lock:
            if job.terminal and event != "end":
                return
            job.last_seq += 1
            item = JobEvent(seq=job.last_seq, type=event, payload=payload, ts=time.time())
            if len(job.events) == job.events.maxlen:
                job.dropped += 1
            job.events.append(item)
            self._apply_event(job, event, payload)
            queues = list(job.subscribers)

        for queue in queues:
            self._put(queue, item, job.loop)

    def _apply_event(self, job: Job, event: str, payload: dict) -> None:
        phase = payload.get("name") if event == "phase" else _PHASE_BY_EVENT.get(event)
        if phase:
            job.phase = str(phase)
        if event == "scan":
            job.counters["files"] = payload.get("files", 0)
            job.counters["skipped"] = payload.get("skipped", 0)
        elif event == "static_done":
            job.counters["static_findings"] = payload.get("findings", 0)
        elif event == "context":
            job.counters["turn"] = payload.get("turn", 0)
            job.counters["tokens"] = payload.get("tokens", 0)
            job.counters["context_ratio"] = payload.get("ratio", 0)
        elif event == "tool_calls":
            job.counters["tool_calls"] = job.counters.get("tool_calls", 0) + len(
                payload.get("names") or []
            )
        elif event == "eval_case":
            job.counters["case_index"] = payload.get("index", 0)
            job.counters["case_total"] = payload.get("total", 0)
            job.counters["recall"] = payload.get("recall", 0)
        elif event == "compress":
            job.counters["compress"] = job.counters.get("compress", 0) + 1
        elif event == "done":
            job.counters["score"] = payload.get("score")
            job.counters["findings"] = payload.get("findings")

    def _put(
        self,
        queue: asyncio.Queue,
        item: Any,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        def push() -> None:
            try:
                queue.put_nowait(item)
            except asyncio.QueueFull:
                # 慢客户端：丢最旧一条，保证不反压任务线程
                try:
                    queue.get_nowait()
                    queue.put_nowait(item)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

        if loop is not None and loop.is_running():
            try:
                loop.call_soon_threadsafe(push)
                return
            except RuntimeError:  # 事件循环已关闭（进程退出中）
                return
        try:
            queue.put_nowait(item)
        except asyncio.QueueFull:
            pass

    # ------------------------------------------------------------ 查询 / 控制

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self, limit: int = 20) -> list[Job]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
        return jobs[:limit]

    def cancel(self, job_id: str) -> Job | None:
        job = self.get(job_id)
        if job is None:
            return None
        if job.terminal:
            return job
        job.cancel.cancel()
        with job.lock:
            if job.status == "running":
                job.status = "cancelling"
        self.publish(job, "cancel_requested", {"status": job.status})
        return job

    def snapshot(self, job: Job) -> dict:
        with job.lock:
            return {
                "id": job.id,
                "kind": job.kind,
                "status": job.status,
                "phase": job.phase,
                "params": job.params,
                "workspace": job.workspace,
                "elapsed_sec": job.elapsed(),
                "counters": dict(job.counters),
                "last_seq": job.last_seq,
                "dropped": job.dropped,
                "result": job.result,
                "error": job.error,
                "created_at": job.created_at,
                "started_at": job.started_at,
                "finished_at": job.finished_at,
            }

    def subscribe(self, job: Job) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        with job.lock:
            job.subscribers.add(queue)
        return queue

    def unsubscribe(self, job: Job, queue: asyncio.Queue) -> None:
        with job.lock:
            job.subscribers.discard(queue)

    def _reap_locked(self) -> None:
        ttl = self._settings.server.job_ttl_seconds
        now = time.time()
        expired = [
            job_id
            for job_id, job in self._jobs.items()
            if job.terminal and job.finished_at and now - job.finished_at > ttl
        ]
        for job_id in expired:
            self._jobs.pop(job_id, None)
        if len(self._jobs) > 50:
            terminal = sorted(
                (j for j in self._jobs.values() if j.terminal),
                key=lambda j: j.finished_at or 0,
            )
            for job in terminal[: len(self._jobs) - 50]:
                self._jobs.pop(job.id, None)

    def shutdown(self) -> None:
        self._review_pool.shutdown(wait=False, cancel_futures=True)
        self._eval_pool.shutdown(wait=False, cancel_futures=True)


Publisher = Callable[[str, dict], None]

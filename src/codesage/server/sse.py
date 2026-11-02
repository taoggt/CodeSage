"""SSE 编码：快照 + 历史重放 + 心跳。

协议要点：
- 建连立刻发 `snapshot`（无 id，不干扰 Last-Event-ID），前端可立即渲染；
- 有 Last-Event-ID 时只回放更新的历史；若已被环形缓冲挤掉，发 `truncated`，
  前端改为拉一次快照；
- 15s 无事件发注释帧心跳（防代理/浏览器掐空闲连接）；
- 收到终态 end 后主动结束，避免 EventSource 自动重连形成死循环。
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncGenerator

from codesage.server.jobs import Job, JobManager

HEARTBEAT_SECONDS = 15.0


def _frame(event: str, payload: dict, seq: int | None = None) -> str:
    data = json.dumps(payload, ensure_ascii=False, default=str)
    prefix = f"id: {seq}\n" if seq is not None else ""
    return f"{prefix}event: {event}\ndata: {data}\n\n"


async def stream_job_events(
    job: Job,
    manager: JobManager,
    last_event_id: int = 0,
    heartbeat: float = HEARTBEAT_SECONDS,
) -> AsyncGenerator[str, None]:
    queue = manager.subscribe(job)
    try:
        yield "retry: 3000\n\n"
        yield _frame(
            "snapshot",
            {
                "id": job.id,
                "kind": job.kind,
                "status": job.status,
                "phase": job.phase,
                "counters": dict(job.counters),
                "last_seq": job.last_seq,
                "dropped": job.dropped,
            },
        )

        with job.lock:
            buffered = list(job.events)
            dropped = job.dropped

        if buffered:
            first_seq = buffered[0].seq
            if last_event_id and last_event_id < first_seq - 1:
                yield _frame(
                    "truncated",
                    {"dropped": dropped, "last_seq": job.last_seq},
                )
            else:
                for item in buffered:
                    if item.seq > last_event_id:
                        yield item.to_sse()

        if job.terminal:
            return

        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=heartbeat)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                # 心跳期间任务可能已结束
                if job.terminal:
                    return
                continue

            if item is None:
                return
            yield item.to_sse()
            if getattr(item, "type", "") == "end":
                return
    finally:
        manager.unsubscribe(job, queue)

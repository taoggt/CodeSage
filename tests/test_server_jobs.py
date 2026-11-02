"""任务与 SSE 测试：创建 → 实时事件 → 终态 → 取消/并发闸门。"""

from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from codesage.server.jobs import Job


def _wait(client: TestClient, job_id: str, timeout: float = 60.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        snapshot = client.get(f"/api/jobs/{job_id}").json()
        if snapshot["status"] in ("done", "failed", "cancelled"):
            return snapshot
        time.sleep(0.05)
    raise AssertionError(f"任务超时未结束：{job_id}")


def test_review_job_lifecycle(client: TestClient, workspace: Path) -> None:
    created = client.post(
        "/api/jobs", json={"target": str(workspace), "use_llm": False}
    )
    assert created.status_code == 202
    job_id = created.json()["job_id"]

    snapshot = _wait(client, job_id)
    assert snapshot["status"] == "done", snapshot.get("error")
    assert snapshot["phase"] == "persist"
    assert snapshot["counters"]["files"] == 2

    review_id = snapshot["result"]["review_id"]
    assert review_id
    detail = client.get(f"/api/reviews/{review_id}")
    assert detail.status_code == 200
    assert detail.json()["metadata"]["profile"] == "full"
    assert detail.json()["metadata"]["files"]


def test_sse_replays_events_and_closes(client: TestClient, workspace: Path) -> None:
    job_id = client.post(
        "/api/jobs", json={"target": str(workspace), "use_llm": False}
    ).json()["job_id"]
    _wait(client, job_id)

    with client.stream("GET", f"/api/jobs/{job_id}/events") as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        text = "".join(response.iter_text())

    assert "event: snapshot" in text
    assert "event: scan" in text
    assert "event: static_done" in text
    assert "event: end" in text
    # 回放不应重复：同一个 seq 只出现一次
    ids = [line.split(": ")[1] for line in text.splitlines() if line.startswith("id: ")]
    assert len(ids) == len(set(ids))


def test_cancel_finished_job_returns_409(client: TestClient, workspace: Path) -> None:
    job_id = client.post(
        "/api/jobs", json={"target": str(workspace), "use_llm": False}
    ).json()["job_id"]
    _wait(client, job_id)
    response = client.post(f"/api/jobs/{job_id}/cancel")
    assert response.status_code == 409
    assert response.json()["code"] == "job_finished"


def test_concurrency_limit_returns_429(client: TestClient, workspace: Path) -> None:
    settings = client.app.state.settings
    settings.server.max_review_jobs = 1

    manager = client.app.state.jobs
    busy = Job(
        id="busy00000000",
        kind="review",
        params={},
        workspace=str(workspace),
        status="running",
    )
    manager._jobs[busy.id] = busy

    response = client.post(
        "/api/jobs", json={"target": str(workspace), "use_llm": False}
    )
    assert response.status_code == 429
    assert response.json()["code"] == "busy"
    assert response.headers.get("retry-after")


def test_unknown_job_404(client: TestClient) -> None:
    assert client.get("/api/jobs/not-exist").status_code == 404
    assert client.get("/api/jobs/not-exist/events").status_code == 404


def test_invalid_target_is_rejected_before_queueing(
    client: TestClient, tmp_path: Path
) -> None:
    response = client.post(
        "/api/jobs", json={"target": str(tmp_path / "nope"), "use_llm": False}
    )
    assert response.status_code in (403, 404)

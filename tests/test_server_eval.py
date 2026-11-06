"""评测接口测试：mock 运行、结果读取、不污染历史表、A/B 对比。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

DATASET = Path("examples/eval_dataset").resolve()


def _wait(client: TestClient, job_id: str, timeout: float = 120.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        snapshot = client.get(f"/api/jobs/{job_id}").json()
        if snapshot["status"] in ("done", "failed", "cancelled"):
            return snapshot
        time.sleep(0.1)
    raise AssertionError(f"评测超时：{job_id}")


@pytest.mark.skipif(not DATASET.is_dir(), reason="缺少 examples/eval_dataset")
def test_eval_run_does_not_pollute_reviews(client: TestClient, tmp_path: Path) -> None:
    results_dir = tmp_path / "evals"
    before = client.get("/api/reviews").json()["total"]

    created = client.post(
        "/api/eval",
        json={
            "dataset": str(DATASET),
            "tag": "t-mock",
            "mock": True,
            "sample": 2,
            "results_dir": str(results_dir),
        },
    )
    assert created.status_code == 202
    snapshot = _wait(client, created.json()["job_id"])
    assert snapshot["status"] == "done", snapshot.get("error")
    assert snapshot["result"]["eval"]["cases"] == 2

    # persist=False：评测案例不写历史表
    assert client.get("/api/reviews").json()["total"] == before

    listing = client.get("/api/eval/results", params={"results_dir": str(results_dir)}).json()
    assert listing["items"][0]["tag"] == "t-mock"
    assert listing["items"][0]["false_positive_rate"] == pytest.approx(
        1 - listing["items"][0]["precision"], abs=1e-4
    )

    detail = client.get(
        "/api/eval/results/t-mock", params={"results_dir": str(results_dir)}
    ).json()
    assert len(detail["cases"]) == 2
    assert detail["summary"]["mock"] is True


@pytest.mark.skipif(not DATASET.is_dir(), reason="缺少 examples/eval_dataset")
def test_eval_compare(client: TestClient, tmp_path: Path) -> None:
    results_dir = tmp_path / "evals"
    for tag in ("a", "b"):
        job_id = client.post(
            "/api/eval",
            json={
                "dataset": str(DATASET),
                "tag": tag,
                "mock": True,
                "sample": 2,
                "results_dir": str(results_dir),
            },
        ).json()["job_id"]
        _wait(client, job_id)

    compared = client.get(
        "/api/eval/compare",
        params={"a": "a", "b": "b", "results_dir": str(results_dir)},
    )
    assert compared.status_code == 200
    assert compared.json()["tag_a"] == "a"
    assert "metrics" in compared.json()


def test_eval_requires_dataset(client: TestClient) -> None:
    response = client.post(
        "/api/eval",
        json={"dataset": "C:/definitely/not/here", "tag": "x", "mock": True},
    )
    assert response.status_code == 202  # 入队成功
    job_id = response.json()["job_id"]
    snapshot = _wait(client, job_id, timeout=30)
    assert snapshot["status"] == "failed"
    assert snapshot["error"]["code"] == "eval_failed"


# ------------------------------------------------------------ 多语言数据集


def test_discover_lists_java_dataset(client: TestClient) -> None:
    """Java 数据集必须能被自动发现：目录名不叫 eval_dataset 时也要看得到。"""
    datasets = client.get("/api/eval/datasets").json()["items"]
    names = {item["name"] for item in datasets}
    if not Path("examples/eval_dataset_java").is_dir():
        pytest.skip("缺少 examples/eval_dataset_java")
    assert "eval_dataset_java" in names
    java = next(item for item in datasets if item["name"] == "eval_dataset_java")
    assert java["cases"] == 8


def test_dataset_rejects_unsupported_snippet_suffix(tmp_path: Path) -> None:
    """片段后缀不受支持时必须显式报错，而不是让整批评测崩在 pipeline 里。"""
    from codesage.core.errors import HarnessError
    from codesage.harness.dataset import load_dataset

    (tmp_path / "case_001.yaml").write_text(
        "id: c1\ncategory: bug\nsnippet: snippets/case_001.kt\n"
        "ground_truth:/n  line_range: [1, 1]\n",
        encoding="utf-8",
    )
    (tmp_path / "snippets").mkdir()
    (tmp_path / "snippets" / "case_001.kt").write_text("fun main() {}\n", encoding="utf-8")

    with pytest.raises(HarnessError) as exc:
        load_dataset(tmp_path)
    assert "不受支持" in str(exc.value)

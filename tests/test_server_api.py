"""HTTP 接口测试：路径守卫、文件读取、历史、反馈、错题本。"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from codesage.cli.exporter import to_markdown
from codesage.core.config import Settings
from codesage.core.models import Category, Finding, ReviewReport, Severity, Source
from codesage.memory.store import Store


def test_health_and_config_never_leak_api_key(client: TestClient, settings: Settings) -> None:
    settings.llm.api_key = "sk-secret-value"

    health = client.get("/api/health").json()
    assert health["status"] == "ok"
    assert health["llm_configured"] is True

    config = client.get("/api/config").json()
    serialized = json.dumps(config, ensure_ascii=False)
    assert "sk-secret-value" not in serialized
    assert config["llm"]["configured"] is True


def test_open_workspace_and_tree(client: TestClient, workspace: Path) -> None:
    info = client.post("/api/workspace/open", json={"path": str(workspace)}).json()
    assert info["files"] == 2  # a.py / b.py
    assert info["name"] == "proj"
    assert info["analyzer_ready"]["ast_rule"] is True

    tree = client.get("/api/workspace/tree", params={"root": str(workspace)}).json()
    names = {entry["name"] for entry in tree["entries"]}
    assert {"a.py", "b.py", "readme.md"} <= names
    assert ".env" not in names  # 敏感文件不出现在树里
    assert "blob.bin" not in names  # 非文本类型不列出


def test_file_read_window(client: TestClient, workspace: Path) -> None:
    data = client.get(
        "/api/workspace/file", params={"root": str(workspace), "path": "b.py"}
    ).json()
    assert "os.system" in data["content"]
    assert data["total_lines"] == 5
    assert data["language"] == "python"


def test_path_traversal_is_rejected(
    client: TestClient, workspace: Path, tmp_path: Path
) -> None:
    (tmp_path / "secret.py").write_text("TOKEN='x'", encoding="utf-8")
    response = client.get(
        "/api/workspace/file", params={"root": str(workspace), "path": "../secret.py"}
    )
    assert response.status_code == 403
    assert response.json()["code"] == "path_outside_root"


def test_sensitive_and_binary_files_are_rejected(
    client: TestClient, workspace: Path
) -> None:
    env = client.get(
        "/api/workspace/file", params={"root": str(workspace), "path": ".env"}
    )
    assert env.status_code == 403
    assert env.json()["code"] == "path_forbidden"

    binary = client.get(
        "/api/workspace/file", params={"root": str(workspace), "path": "blob.bin"}
    )
    assert binary.status_code == 415


def test_unregistered_root_is_rejected(
    client: TestClient, workspace: Path, tmp_path: Path
) -> None:
    other = tmp_path / "other"
    other.mkdir()
    (other / "x.py").write_text("x = 1", encoding="utf-8")
    response = client.get(
        "/api/workspace/tree", params={"root": str(other)}
    )
    assert response.status_code == 403


def _seed_review(client: TestClient, workspace: Path) -> tuple[int, ReviewReport]:
    settings: Settings = client.app.state.settings
    store = Store(Path(settings.review.store_path))
    report = ReviewReport(target=str(workspace / "a.py"), duration_sec=1.25)
    report.findings = [
        Finding(
            file="a.py",
            line_start=1,
            line_end=1,
            category=Category.BUG,
            severity=Severity.MAJOR,
            title="可变默认参数",
            description="默认值在函数定义时求值",
            suggestion="改为 None 再赋值",
            source=Source.AST_RULE,
            rule_id="CG001",
        )
    ]
    review_id = store.save_review(report, project=str(workspace))
    store.close()
    return review_id, report


def test_review_list_detail_and_export(client: TestClient, workspace: Path) -> None:
    review_id, report = _seed_review(client, workspace)

    listing = client.get("/api/reviews").json()
    assert listing["total"] == 1
    assert listing["items"][0]["id"] == review_id
    assert listing["items"][0]["by_severity"]["major"] == 1

    detail = client.get(f"/api/reviews/{review_id}").json()
    assert detail["findings"][0]["rule_id"] == "CG001"
    assert detail["review"]["project"] == str(workspace)

    exported = client.get(f"/api/reviews/{review_id}/export", params={"format": "md"})
    assert exported.status_code == 200
    assert exported.text == to_markdown(ReviewReport.from_dict(detail))

    assert client.get("/api/reviews/99999").status_code == 404


def test_review_compare(client: TestClient, workspace: Path) -> None:
    settings: Settings = client.app.state.settings
    store = Store(Path(settings.review.store_path))
    before = ReviewReport(target=str(workspace / "a.py"))
    before.findings = [
        Finding(
            file="a.py",
            line_start=1,
            line_end=1,
            category=Category.BUG,
            severity=Severity.MAJOR,
            title="问题 A",
            source=Source.AST_RULE,
        ),
        Finding(
            file="a.py",
            line_start=5,
            line_end=5,
            category=Category.BUG,
            severity=Severity.MINOR,
            title="问题 B",
            source=Source.AST_RULE,
        ),
    ]
    after = ReviewReport(target=str(workspace / "a.py"))
    after.findings = [before.findings[0]]
    before_id = store.save_review(before, project=str(workspace))
    after_id = store.save_review(after, project=str(workspace))
    store.close()

    result = client.get(
        "/api/reviews/compare", params={"a": before_id, "b": after_id}
    ).json()
    assert result["counts"]["resolved"] == 1
    assert result["counts"]["added"] == 0
    assert result["resolved"][0]["title"] == "问题 B"


def test_feedback_closes_loop_in_one_call(client: TestClient, workspace: Path) -> None:
    review_id, report = _seed_review(client, workspace)
    finding_id = report.findings[0].id

    response = client.post(
        "/api/feedback",
        json={
            "finding_id": finding_id,
            "verdict": "wrong",
            "note": "测试用例故意这么写",
            "review_id": review_id,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["located"] is True
    assert payload["lesson_created"] is True
    assert payload["lesson_id"] > 0

    lessons = client.get("/api/lessons", params={"project": str(workspace)}).json()
    assert lessons["total"] == 1
    assert "【误报案例】" in lessons["items"][0]["content"]

    preview = client.get("/api/lessons/preview", params={"project": str(workspace)}).json()
    assert "【误报案例】" in preview["block"]
    assert preview["chars"] <= preview["limit_chars"]


def test_lesson_manual_missed_and_delete(client: TestClient, workspace: Path) -> None:
    created = client.post(
        "/api/lessons/missed",
        json={
            "file": "a.py",
            "line": 3,
            "title": "未检出资源泄漏",
            "category": "bug",
            "project": str(workspace),
        },
    ).json()
    assert created["id"] > 0
    assert "【漏报案例】a.py:3" in created["content"]

    duplicate = client.post(
        "/api/lessons/missed",
        json={
            "file": "a.py",
            "line": 3,
            "title": "未检出资源泄漏",
            "category": "bug",
            "project": str(workspace),
        },
    ).json()
    assert duplicate["dup"] is True

    assert client.delete(f"/api/lessons/{created['id']}").json()["deleted"] == 1
    assert client.get("/api/lessons", params={"project": str(workspace)}).json()["total"] == 0


def test_stats_overview(client: TestClient, workspace: Path) -> None:
    _seed_review(client, workspace)
    stats = client.get("/api/stats").json()
    assert stats["reviews"] == 1
    assert stats["lessons"] == 0


def test_status_points_to_subdirectory_reviews(
    client: TestClient, workspace: Path
) -> None:
    """根目录没审过，但 src/ 审过——要能提示，而不是干巴巴一句“无记录”。"""
    settings: Settings = client.app.state.settings
    sub = workspace / "pkg"
    sub.mkdir()
    (sub / "x.py").write_text("x = 1\n", encoding="utf-8")

    store = Store(Path(settings.review.store_path))
    store.save_review(ReviewReport(target=str(sub / "x.py")), project=str(sub))
    store.close()

    data = client.get(
        "/api/workspace/status", params={"root": str(workspace)}
    ).json()
    assert data["latest_review"] is None
    assert data["subproject_reviews"]
    assert data["subproject_reviews"][0]["project"] == str(sub)
    assert data["subproject_reviews"][0]["relative"] == "pkg"


def test_lessons_visible_when_queried_with_posix_project(
    client: TestClient, workspace: Path
) -> None:
    """复现 GUI 的真实路径形态：写入用 str(workspace)，查询用 as_posix()。

    修复前：报告页角标显示"已标记误报"，错题本却 0 条（SQL 字符串全等比较）。
    """
    review_id, report = _seed_review(client, workspace)
    client.post(
        "/api/feedback",
        json={
            "finding_id": report.findings[0].id,
            "verdict": "wrong",
            "review_id": review_id,
        },
    )

    posix = str(workspace).replace("\\", "/")
    lessons = client.get("/api/lessons", params={"project": posix}).json()
    assert lessons["total"] == 1
    assert lessons["stats"]["false_positive"] == 1

    feedback = client.get("/api/feedback", params={"project": posix}).json()
    assert feedback["total"] == 1

    preview = client.get("/api/lessons/preview", params={"project": posix}).json()
    assert preview["chars"] > 0

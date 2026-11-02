"""工作区选择：目录浏览 + 原生对话框接口。

原生对话框本身不可自动化测试（会弹模态窗），因此对 dialogs 做替身：
只验证「可用性判定 → 5xx/501 分支」与「返回路径 → 响应结构」。
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from codesage.server.routers import workspace as workspace_router


def test_browse_root_view(client: TestClient) -> None:
    data = client.get("/api/workspace/browse").json()
    assert data["at_root"] is True
    assert data["entries"], "至少应返回一个盘符或根目录"
    assert data["shortcuts"], "应提供主目录/桌面等快捷位置"


def test_browse_lists_directories_only(client: TestClient, workspace: Path) -> None:
    (workspace / "pkg" / "inner").mkdir(parents=True)
    data = client.get(
        "/api/workspace/browse", params={"path": str(workspace)}
    ).json()

    names = [entry["name"] for entry in data["entries"]]
    assert names == ["pkg"]  # 文件不列；隐藏目录不列
    assert data["parent"] == str(workspace.parent)
    assert data["at_root"] is False


def test_browse_missing_path_returns_404(client: TestClient, tmp_path: Path) -> None:
    response = client.get(
        "/api/workspace/browse", params={"path": str(tmp_path / "not-here")}
    )
    assert response.status_code == 404
    assert response.json()["code"] == "invalid_path"


def test_unregister_root_only_forgets_the_record(
    client: TestClient, workspace: Path
) -> None:
    """删除"最近打开"只移除登记，不删磁盘目录。"""
    assert client.get("/api/workspace/tree", params={"root": str(workspace)}).status_code == 200

    response = client.delete("/api/workspace/roots", params={"path": str(workspace)})
    assert response.status_code == 200
    assert response.json()["removed"] is True
    assert str(workspace) not in response.json()["items"]

    # 登记被移除后，文件接口不再放行；但目录本身仍然存在
    assert workspace.is_dir()
    assert client.get("/api/workspace/tree", params={"root": str(workspace)}).status_code == 403


def test_pick_returns_501_when_dialog_unavailable(
    client: TestClient, monkeypatch
) -> None:
    monkeypatch.setattr(workspace_router.dialogs, "platform_supported", lambda: False)
    response = client.post("/api/workspace/pick", json={})
    assert response.status_code == 501
    assert response.json()["code"] == "dialog_unavailable"


def test_pick_returns_selected_path(
    client: TestClient, workspace: Path, monkeypatch
) -> None:
    monkeypatch.setattr(workspace_router.dialogs, "platform_supported", lambda: True)
    monkeypatch.setattr(
        workspace_router.dialogs, "ask_directory", lambda initial=None: str(workspace)
    )
    response = client.post(
        "/api/workspace/pick", json={"initial": str(workspace)}
    )
    assert response.status_code == 200
    assert response.json() == {"path": str(workspace), "cancelled": False}


def test_pick_handles_cancel(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(workspace_router.dialogs, "platform_supported", lambda: True)
    monkeypatch.setattr(
        workspace_router.dialogs, "ask_directory", lambda initial=None: None
    )
    response = client.post("/api/workspace/pick", json={})
    assert response.status_code == 200
    assert response.json() == {"path": None, "cancelled": True}

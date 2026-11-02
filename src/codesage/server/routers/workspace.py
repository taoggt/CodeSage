"""工作区：打开目录、文件树、源码窗口、审查状态、零 Token 预检。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Query

from codesage.server import dialogs
from codesage.server.deps import RegistryDep, SettingsDep, StoreDep
from codesage.server.errors import ApiError
from codesage.server.schemas import OpenWorkspaceRequest, PickDirectoryRequest
from codesage.services import workspace_service as ws

router = APIRouter(prefix="/workspace", tags=["workspace"])


def _root(registry: RegistryDep, root: str) -> Path:
    return registry.get(root)


@router.get("/roots")
def list_roots(registry: RegistryDep) -> dict:
    return {"items": registry.list()}


@router.delete("/roots")
def remove_root(registry: RegistryDep, path: str = Query(...)) -> dict:
    """从"最近打开"里移除一条记录（不会删除磁盘目录）。"""
    removed = registry.unregister(path)
    return {"removed": removed, "items": registry.list()}


@router.get("/browse")
def browse(path: str | None = Query(None)) -> dict:
    """目录浏览器（只列目录），用于在网页里点选工作区。"""
    return ws.browse(path)


@router.post("/pick")
def pick_directory(payload: PickDirectoryRequest) -> dict:
    """弹出本机系统目录选择框（浏览器拿不到绝对路径，只能由后端弹）。"""
    if not dialogs.platform_supported():
        raise ApiError(
            501,
            "dialog_unavailable",
            "当前环境不支持系统对话框，请改用「浏览目录」",
        )
    try:
        chosen = dialogs.ask_directory(payload.initial)
    except dialogs.DialogUnavailable as exc:
        raise ApiError(501, "dialog_unavailable", str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise ApiError(500, "dialog_failed", f"打开系统对话框失败：{exc}") from exc
    return {"path": chosen, "cancelled": chosen is None}


@router.post("/open")
def open_workspace(
    payload: OpenWorkspaceRequest,
    settings: SettingsDep,
    registry: RegistryDep,
) -> dict:
    return ws.open_workspace(payload.path, settings, registry)


@router.get("/tree")
def tree(
    registry: RegistryDep,
    root: str = Query(...),
    path: str = Query(""),
    depth: int = Query(1, ge=1, le=3),
) -> dict:
    return ws.list_tree(_root(registry, root), path, depth)


@router.get("/file")
def file_content(
    registry: RegistryDep,
    root: str = Query(...),
    path: str = Query(...),
    start: int | None = Query(None, ge=1),
    end: int | None = Query(None, ge=1),
) -> dict:
    return ws.read_file(_root(registry, root), path, start, end)


@router.get("/status")
def status(registry: RegistryDep, store: StoreDep, root: str = Query(...)) -> dict:
    return ws.workspace_status(_root(registry, root), store)


@router.get("/scan-preview")
def scan_preview(
    registry: RegistryDep,
    settings: SettingsDep,
    root: str = Query(...),
    target: str | None = Query(None),
) -> dict:
    return ws.scan_preview(_root(registry, root), target, settings)

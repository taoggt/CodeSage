"""FastAPI 依赖：从 app.state 取出共享对象。

注意 get_store 是"每请求一个新连接"（yield 依赖），不缓存 Store 实例。
"""

from __future__ import annotations

from typing import Annotated, Iterator

from fastapi import Depends, Request

from codesage.core.config import Settings
from codesage.memory.store import Store
from codesage.server.jobs import JobManager
from codesage.services.store_factory import open_store
from codesage.services.workspace_service import WorkspaceRegistry


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_registry(request: Request) -> WorkspaceRegistry:
    return request.app.state.registry


def get_jobs(request: Request) -> JobManager:
    return request.app.state.jobs


SettingsDep = Annotated[Settings, Depends(get_settings)]
RegistryDep = Annotated[WorkspaceRegistry, Depends(get_registry)]
JobsDep = Annotated[JobManager, Depends(get_jobs)]


def get_store(settings: SettingsDep) -> Iterator[Store]:
    with open_store(settings) as store:
        yield store


StoreDep = Annotated[Store, Depends(get_store)]

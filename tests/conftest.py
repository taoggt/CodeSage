"""测试公共夹具：临时存储 + 临时工作区 + TestClient。

原则：**任何测试都不碰用户真实的 ~/.codesage/store.db**。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from codesage.core.config import Settings
from codesage.server.app import create_app
from codesage.services.workspace_service import WorkspaceRegistry

SAMPLE_FILES = {
    "a.py": "def collect(items=[]):\n    return items\n",
    "b.py": "import os\n\n\ndef run(cmd):\n    os.system(cmd)\n",
}


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    config = Settings()
    config.review.store_path = str(tmp_path / "store.db")
    config.llm.api_key = ""
    return config


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    for name, code in SAMPLE_FILES.items():
        (root / name).write_text(code, encoding="utf-8")
    (root / "readme.md").write_text("# demo", encoding="utf-8")
    (root / ".env").write_text("SECRET=1", encoding="utf-8")
    (root / "blob.bin").write_bytes(b"\x00\x01\x02")
    return root


@pytest.fixture
def registry(workspace: Path) -> WorkspaceRegistry:
    reg = WorkspaceRegistry()
    reg.register(workspace)
    return reg


@pytest.fixture
def client(settings: Settings, registry: WorkspaceRegistry):
    app = create_app(settings, registry, enable_spa=False)
    # Host 必须是回环地址，否则会被 LocalGuardMiddleware 拒绝
    with TestClient(app, base_url="http://127.0.0.1") as test_client:
        yield test_client

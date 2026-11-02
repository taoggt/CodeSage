"""CLI 与安全边界测试：ui 命令可用、只允许回环地址。"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from codesage.cli.main import app
from codesage.server.security import ensure_loopback_host, is_loopback


def test_ui_command_is_registered() -> None:
    result = CliRunner().invoke(app, ["ui", "--help"])
    assert result.exit_code == 0
    assert "8787" in result.stdout
    assert "--dev" in result.stdout


def test_loopback_guard_rejects_public_bind() -> None:
    with pytest.raises(ValueError):
        ensure_loopback_host("0.0.0.0")
    with pytest.raises(ValueError):
        ensure_loopback_host("192.168.1.10")

    assert ensure_loopback_host("127.0.0.1") == "127.0.0.1"
    assert ensure_loopback_host("localhost") == "localhost"


def test_is_loopback_variants() -> None:
    assert is_loopback("127.0.0.1")
    assert is_loopback("::1")
    assert not is_loopback("example.com")


def test_host_guard_blocks_foreign_host(client) -> None:
    response = client.get("/api/health", headers={"host": "evil.example.com"})
    assert response.status_code == 403
    assert response.json()["code"] == "host_not_allowed"


def test_origin_guard_blocks_cross_site_write(client, workspace) -> None:
    response = client.post(
        "/api/workspace/open",
        json={"path": str(workspace)},
        headers={"origin": "https://evil.example.com"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "origin_not_allowed"


def test_origin_allows_localhost(client, workspace) -> None:
    response = client.post(
        "/api/workspace/open",
        json={"path": str(workspace)},
        headers={"origin": "http://127.0.0.1:8787"},
    )
    assert response.status_code == 200

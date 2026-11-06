"""配置写回：白名单、热更新、落盘正确性与非法输入拦截。

这些用例确保三件容易被后续改动破坏的事：
1. api_key / server 段永远不能从网页写入；
2. 保存后运行中的 Settings 立刻生效（不是只改文件）；
3. 三区阈值的分母是工作预算，不是模型标称窗口。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from codesage.core.config import (
    BudgetSettings,
    Settings,
    context_window_for,
    set_config_file,
)
from codesage.llm.budget import BudgetState, TokenBudget
from codesage.server.app import create_app
from codesage.services.config_service import config_view, save_config


@pytest.fixture
def config_path(tmp_path: Path):
    """把配置写回指向临时文件，避免污染仓库根目录的 codesage.toml。"""
    path = tmp_path / "codesage.toml"
    set_config_file(path)
    try:
        yield path
    finally:
        set_config_file(None)


@pytest.fixture
def settings(config_path: Path, tmp_path: Path) -> Settings:
    instance = Settings()
    instance.review.store_path = str(tmp_path / "store.db")
    instance.llm.api_key = "sk-should-never-be-written"
    return instance


@pytest.fixture
def client(settings: Settings):
    app = create_app(settings, enable_spa=False)
    with TestClient(app, base_url="http://127.0.0.1") as test_client:
        yield test_client


# --------------------------------------------------------------- HTTP 层


def test_put_config_persists_and_hot_applies(
    client: TestClient, settings: Settings, config_path: Path
) -> None:
    response = client.put("/api/config", json={"budget": {"budget_window": 200_000}})
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["budget"]["budget_window"] == 200_000
    assert body["budget"]["effective_budget_window"] == 200_000

    # 热更新：运行中的 Settings 实例（JobManager 持有的那个）必须同步变化
    assert settings.budget.budget_window == 200_000

    text = config_path.read_text(encoding="utf-8")
    assert "budget_window = 200000" in text
    assert "sk-should-never-be-written" not in text


def test_put_config_rejects_api_key_and_server_section(client: TestClient) -> None:
    secret = client.put("/api/config", json={"llm": {"api_key": "sk-leak"}})
    assert secret.status_code == 400
    assert "api_key" in secret.json()["message"]

    server = client.put("/api/config", json={"server": {"port": 9999}})
    assert server.status_code == 400
    assert "server" in server.json()["message"]


def test_put_config_rejects_illegal_thresholds(client: TestClient) -> None:
    response = client.put(
        "/api/config",
        json={"budget": {"soft_ratio": 0.99, "hard_ratio": 0.8}},
    )
    assert response.status_code == 400
    assert "校验失败" in response.json()["message"]


def test_get_config_never_leaks_api_key(client: TestClient) -> None:
    body = client.get("/api/config").json()
    assert body["llm"]["configured"] is True
    assert "api_key" not in body["llm"]
    assert "sk-should-never-be-written" not in str(body)


# ------------------------------------------------------------ 服务层语义


def test_budget_window_cannot_exceed_model_context(settings: Settings, config_path: Path) -> None:
    save_config(
        settings,
        {"budget": {"context_window": 100_000, "budget_window": 500_000}},
    )
    assert settings.budget.budget_window == 500_000
    assert settings.budget.effective_budget_window == 100_000


def test_saved_config_survives_reload(
    settings: Settings, config_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # 切到临时目录：否则会读到仓库根的 .env（它的优先级高于 toml）
    monkeypatch.chdir(tmp_path)
    save_config(settings, {"llm": {"model_strong": "deepseek-v4-pro"}})
    reloaded = Settings()  # 重新走 env + toml + 默认值
    assert reloaded.llm.model_strong == "deepseek-v4-pro"
    assert reloaded.budget.budget_window == settings.budget.budget_window


def test_env_locked_field_is_rejected(
    settings: Settings, config_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """被 .env 固定的字段必须报错，而不是静默忽略（否则重启后又变回去）。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "CODESAGE_BUDGET__BUDGET_WINDOW=50000\n", encoding="utf-8"
    )
    before = settings.budget.budget_window
    with pytest.raises(ValueError, match="不会生效"):
        save_config(settings, {"budget": {"budget_window": 90_000}})
    assert settings.budget.budget_window == before


def test_config_view_exposes_suggestion_and_editable_map(settings: Settings) -> None:
    view = config_view(settings)
    assert view["suggested_context_window"] == 1_000_000
    assert "budget_window" in view["editable"]["budget"]
    assert "api_key" not in view["editable"].get("llm", [])
    assert view["config_file"].endswith("codesage.toml")


# ------------------------------------------------- 预算分母（本次改动的核心）


def test_thresholds_are_measured_against_budget_window() -> None:
    budget = TokenBudget(BudgetSettings(context_window=1_000_000, budget_window=120_000))
    assert budget.budget_window == 120_000

    # 72k / 120k = 60% → 软阈值边界
    assert budget.ratio(72_000) == pytest.approx(0.6)
    assert budget.state(72_000) is BudgetState.SOFT
    # 同样的 72k 若拿 1M 当分母只有 7.2%，压缩永远不会触发
    assert budget.state(72_000) is not BudgetState.OK
    # 96k / 120k = 80% → 强制压缩
    assert budget.state(96_000) is BudgetState.HARD
    # 114k / 120k = 95% → 熔断
    assert budget.state(114_000) is BudgetState.ABORT
    assert budget.state(60_000) is BudgetState.OK


def test_zero_budget_window_falls_back_to_context_window() -> None:
    budget = TokenBudget(BudgetSettings(context_window=80_000, budget_window=0))
    assert budget.budget_window == 80_000


def test_model_context_inference() -> None:
    assert context_window_for("deepseek-flash") == 1_000_000
    assert context_window_for("deepseek-flash-1017") == 1_000_000
    assert context_window_for("DeepSeek-V4-Pro") == 1_000_000
    assert context_window_for("deepseek-chat") == 128_000
    assert context_window_for("some-unknown-model") == 128_000
    assert context_window_for("") == 128_000


# ------------------------------------------------------------ 多语言配置


def test_languages_is_editable_and_hot_applies(
    client: TestClient, settings: Settings, config_path: Path
) -> None:
    """languages 必须在白名单里：漏了它 GUI 保存会直接 400。

    （历史坑：白名单是硬编码的，新增字段忘了加就变成"设置页有这个选项、
    点了保存却报错"。）
    """
    response = client.put("/api/config", json={"review": {"languages": "python,java"}})
    assert response.status_code == 200, response.text

    assert settings.review.languages == "python,java"
    assert "languages = \"python,java\"" in config_path.read_text(encoding="utf-8")
    assert "languages" in response.json()["editable"]["review"]


def test_invalid_language_config_is_rejected(client: TestClient) -> None:
    """非法语言必须报错，不能静默退回 auto（否则用户会以为生效了）。"""
    response = client.put("/api/config", json={"review": {"languages": "kotlin"}})
    assert response.status_code == 400
    assert "校验失败" in response.json()["message"]


def test_language_config_survives_reload(
    settings: Settings, config_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    save_config(settings, {"review": {"languages": "java"}})
    assert Settings().review.languages == "java"

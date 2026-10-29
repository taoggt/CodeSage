"""配置加载：环境变量 + codesage.toml + 代码默认值。

优先级：环境变量 > codesage.toml > 默认值。

环境变量命名规则（pydantic-settings 约定）：
    CODESAGE_LLM__API_KEY=xxx      → settings.llm.api_key
    CODESAGE_BUDGET__CONTEXT_WINDOW=64000 → settings.budget.context_window
（双下划线 __ 表示嵌套层级）
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)


class LLMSettings(BaseModel):
    """LLM 接入配置（OpenAI 兼容协议，默认指向 DeepSeek）。"""

    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    model_strong: str = "deepseek-chat"  # 深度审查
    model_fast: str = "deepseek-chat"    # 工具决策 / 摘要压缩
    temperature: float = 0.2             # 代码审查场景需要稳定输出
    max_output_tokens: int = 4096
    timeout: float = 120.0


class BudgetSettings(BaseModel):
    """Token 预算与压缩阈值（三区记忆模型的触发参数）。"""

    context_window: int = 64_000
    soft_ratio: float = 0.6   # ≥60%：回合边界压缩
    hard_ratio: float = 0.8   # ≥80%：调用前强制压缩
    abort_ratio: float = 0.95 # ≥95%：熔断，输出已有发现
    input_price_per_m: float = 2.0   # 元 / 百万输入 token（DeepSeek 参考价）
    output_price_per_m: float = 8.0  # 元 / 百万输出 token


class ReviewSettings(BaseModel):
    """审查行为配置。"""

    max_turns: int = 12                    # ReAct 循环轮次上限
    active_window_turns: int = 4           # 记忆活跃区保留轮数
    max_file_lines: int = 2000             # 大文件预过滤阈值
    max_chunk_lines: int = 400             # 单块最大行数
    tool_output_limit: int = 500           # 工具单次输出行数上限
    max_candidates_in_prompt: int = 40     # 初始上下文注入的候选发现上限
    store_path: str = ""                   # 历史/错题本存储；空 = 用户级默认 ~/.codesage/store.db
    ignore_dirs: list[str] = Field(
        default_factory=lambda: [
            ".git", "__pycache__", ".venv", "venv", "node_modules",
            ".mypy_cache", ".pytest_cache", ".ruff_cache",
            "build", "dist", ".eggs", ".codesage",
        ]
    )


class Settings(BaseSettings):
    """CodeSage 全局配置。"""

    model_config = SettingsConfigDict(
        env_prefix="CODESAGE_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # 注意：codesage.toml 由 settings_customise_sources 动态探测后加载
        # （不放 model_config 的 toml_file，避免文件不存在时的配置告警）
    )

    llm: LLMSettings = Field(default_factory=LLMSettings)
    budget: BudgetSettings = Field(default_factory=BudgetSettings)
    review: ReviewSettings = Field(default_factory=ReviewSettings)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """数据源顺序：显式初始化 > 环境变量 > .env > codesage.toml。"""
        sources: list[PydanticBaseSettingsSource] = [
            init_settings,
            env_settings,
            dotenv_settings,
        ]
        toml_path = Path("codesage.toml")
        if toml_path.exists():
            sources.append(
                TomlConfigSettingsSource(settings_cls, toml_file=str(toml_path))
            )
        return tuple(sources)

    @property
    def llm_configured(self) -> bool:
        """是否具备调用 LLM 的条件。"""
        return bool(self.llm.api_key.strip())


# 常见环境变量兜底顺序（用户不必记住 CODESAGE_ 前缀）
_FALLBACK_API_KEY_ENVS = ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "DASHSCOPE_API_KEY")


def load_settings() -> Settings:
    """加载配置并做 API Key 兜底。

    若 CODESAGE_LLM__API_KEY 未设置，依次尝试常见环境变量，
    降低上手门槛（例如用户已习惯导出 DEEPSEEK_API_KEY）。
    """
    settings = Settings()
    if not settings.llm.api_key:
        for env_name in _FALLBACK_API_KEY_ENVS:
            value = os.environ.get(env_name, "").strip()
            if value:
                settings.llm.api_key = value
                break
    return settings

"""配置加载：环境变量 + codesage.toml + 代码默认值。

优先级：环境变量 > codesage.toml > 默认值。

环境变量命名规则（pydantic-settings 约定）：
    CODESAGE_LLM__API_KEY=xxx                    → settings.llm.api_key
    CODESAGE_BUDGET__BUDGET_WINDOW=120000        → settings.budget.budget_window
（双下划线 __ 表示嵌套层级）

两个窗口的分工（本文件最重要的设计）：
    context_window  模型标称上限，随模型走，用于校验与展示；
    budget_window   单次审查真正可用的工作预算，三区阈值以它为分母。
混为一谈会让压缩机制形同虚设：比例阈值会随标称窗口一起放大，
而实际上下文峰值由任务决定，并不随窗口变化。
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

# 已知模型的标称上下文上限（token）。
# 存在这张表的意义：换模型时窗口自动跟随，用户不必自己去查文档手填。
# 未登记的模型走 fallback，仍可在 GUI / toml 里手动指定。
MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "deepseek-flash": 1_000_000,
    "deepseek-v4-flash": 1_000_000,          # 兼容旧名，已由 V4.1-Flash 承接
    "deepseek-v4-flash-vision-exp": 1_000_000,
    "deepseek-v4-pro": 1_000_000,
    "deepseek-chat": 128_000,                # 别名于 2026-07-24 退役，仅作兼容
    "deepseek-reasoner": 128_000,
}

DEFAULT_CONTEXT_WINDOW = 1_000_000
UNKNOWN_MODEL_CONTEXT_WINDOW = 128_000


def context_window_for(model: str) -> int:
    """按模型名推导标称上下文上限；未知模型给保守值。

    先精确匹配，再前缀匹配（覆盖 deepseek-flash-1017 这类带版本后缀的名字）。
    """
    name = model.strip().lower()
    if not name:
        return UNKNOWN_MODEL_CONTEXT_WINDOW
    if name in MODEL_CONTEXT_WINDOWS:
        return MODEL_CONTEXT_WINDOWS[name]
    for known, window in MODEL_CONTEXT_WINDOWS.items():
        if name.startswith(known):
            return window
    return UNKNOWN_MODEL_CONTEXT_WINDOW


class LLMSettings(BaseModel):
    """LLM 接入配置（OpenAI 兼容协议，默认指向 DeepSeek）。"""

    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    model_strong: str = "deepseek-flash"  # 深度审查（V4.1-Flash，1M 上下文）
    model_fast: str = "deepseek-flash"    # 工具决策 / 摘要压缩
    temperature: float = 0.2              # 代码审查场景需要稳定输出
    max_output_tokens: int = 8192         # 单次输出上限；够覆盖一份完整审查报告
    timeout: float = 120.0


class BudgetSettings(BaseModel):
    """Token 预算与压缩阈值（三区记忆模型的触发参数）。

    context_window 是模型标称上限，budget_window 才是审查真正被允许使用的额度。
    调大 context_window 不会让审查更准，只会把压缩触发点推远、把单轮成本抬高。
    """

    context_window: int = DEFAULT_CONTEXT_WINDOW  # 模型标称上限，仅校验与展示
    budget_window: int = 120_000                  # 单次审查工作预算 ← 三区阈值的分母
    soft_ratio: float = 0.6   # ≥60% 预算：回合边界压缩
    hard_ratio: float = 0.8   # ≥80% 预算：调用前强制压缩
    abort_ratio: float = 0.95 # ≥95% 预算：熔断，输出已有发现
    input_price_per_m: float = 2.2   # 元 / 百万输入 token（DeepSeek V4 峰时价）
    output_price_per_m: float = 8.6  # 元 / 百万输出 token

    @field_validator("context_window", "budget_window")
    @classmethod
    def _non_negative(cls, value: int) -> int:
        return max(0, value)

    @model_validator(mode="after")
    def _ordered_ratios(self) -> "BudgetSettings":
        if not (0 < self.soft_ratio < self.hard_ratio < self.abort_ratio <= 1):
            raise ValueError(
                "阈值必须满足 0 < soft_ratio < hard_ratio < abort_ratio ≤ 1，"
                f"当前为 {self.soft_ratio}/{self.hard_ratio}/{self.abort_ratio}"
            )
        return self

    @property
    def effective_budget_window(self) -> int:
        """实际生效的工作预算：不得超过模型标称上限。

        budget_window 留 0 表示"未单独设定"，此时退回 context_window。
        """
        candidates = [w for w in (self.budget_window, self.context_window) if w > 0]
        return min(candidates) if candidates else 0


class ReviewSettings(BaseModel):
    """审查行为配置。"""

    max_turns: int = 12                    # ReAct 循环轮次上限
    active_window_turns: int = 4           # 记忆活跃区保留轮数
    max_file_lines: int = 2000             # 大文件预过滤阈值
    max_chunk_lines: int = 400             # 单块最大行数
    tool_output_limit: int = 500           # 工具单次输出行数上限
    max_candidates_in_prompt: int = 40     # 初始上下文注入的候选发现上限
    # 审查语言范围：auto（按目录实际存在的语言）/ python / java / python,java
    languages: str = "auto"
    store_path: str = ""                   # 历史/错题本存储；空 = 用户级默认 ~/.codesage/store.db
    ignore_dirs: list[str] = Field(
        default_factory=lambda: [
            ".git", "__pycache__", ".venv", "venv", "node_modules",
            ".mypy_cache", ".pytest_cache", ".ruff_cache",
            "build", "dist", ".eggs", ".codesage",
        ]
    )

    @field_validator("languages")
    @classmethod
    def _validate_languages(cls, value: str) -> str:
        """提前校验语言取值。

        为什么在配置层就拦：`Settings` 用了 `extra="ignore"`，写错的字段会被
        静默丢弃；如果语言值也不校验，用户会遇到"配了 java 却没生效"这种
        最难排查的故障（没有报错、行为又不对）。
        """
        from codesage.core.languages import parse_language_mode

        mode, _ = parse_language_mode(value)   # 非法值抛 ValueError → pydantic 报配置错误
        return mode


class ServerSettings(BaseModel):
    """浏览器 GUI 配置（仅监听回环地址）。"""

    host: str = "127.0.0.1"
    port: int = 8787
    open_browser: bool = True
    max_review_jobs: int = 2      # 并发审查任务上限（超过直接 429，不排队）
    max_eval_jobs: int = 1        # 评测任务串行
    job_ttl_seconds: int = 7200   # 终态任务在内存中的保留时长
    event_buffer: int = 2000      # 单任务事件环形缓冲大小


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
    server: ServerSettings = Field(default_factory=ServerSettings)

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
        toml_path = _TOML_OVERRIDE or Path("codesage.toml")
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

# 显式指定的 toml 路径（GUI / 测试用；None = 按当前工作目录探测）
_TOML_OVERRIDE: Path | None = None


def set_config_file(path: Path | str | None) -> None:
    """指定 codesage.toml 路径，供非 CLI 进程（GUI/服务端）摆脱 CWD 依赖。"""
    global _TOML_OVERRIDE
    _TOML_OVERRIDE = Path(path) if path else None


def active_config_path() -> Path:
    """当前生效的 codesage.toml 路径（GUI 保存配置时写回这个文件）。

    与 settings_customise_sources 的探测逻辑保持一致：优先显式指定，
    否则退回运行目录下的 codesage.toml。
    """
    return _TOML_OVERRIDE or Path("codesage.toml")


def load_settings(config_file: Path | str | None = None) -> Settings:
    """加载配置并做 API Key 兜底。

    若 CODESAGE_LLM__API_KEY 未设置，依次尝试常见环境变量，
    降低上手门槛（例如用户已习惯导出 DEEPSEEK_API_KEY）。

    config_file 显式指定时优先使用该 toml 文件（不传则维持 CWD 探测的旧行为）。
    """
    if config_file is not None:
        set_config_file(config_file)
    settings = Settings()
    if not settings.llm.api_key:
        for env_name in _FALLBACK_API_KEY_ENVS:
            value = os.environ.get(env_name, "").strip()
            if value:
                settings.llm.api_key = value
                break
    return settings

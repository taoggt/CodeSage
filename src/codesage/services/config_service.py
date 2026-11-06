"""配置读写服务：GUI 设置页的保存逻辑（CLI 不走这里）。

分层纪律：本模块不 import fastapi，只做「校验 → 落盘 → 就地热更新」。

为什么不把 codesage.toml 直接交给前端编辑：
- API Key 永远不进网页，白名单里没有 api_key；
- server 段（端口、并发数、事件缓冲）改了要重建线程池，不在运行期开放；
- 落盘前必须过 pydantic 校验，否则非法阈值写进文件后下次启动直接起不来。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from codesage.core.config import (
    Settings,
    active_config_path,
    context_window_for,
)

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 兜底
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:
        tomllib = None  # type: ignore[assignment]

# 允许从 GUI 修改的字段（白名单）。api_key 与整个 server 段刻意不在其中。
EDITABLE_FIELDS: dict[str, tuple[str, ...]] = {
    "llm": (
        "base_url",
        "model_strong",
        "model_fast",
        "temperature",
        "max_output_tokens",
        "timeout",
    ),
    "budget": (
        "context_window",
        "budget_window",
        "soft_ratio",
        "hard_ratio",
        "abort_ratio",
        "input_price_per_m",
        "output_price_per_m",
    ),
    "review": (
        "max_turns",
        "active_window_turns",
        "max_file_lines",
        "max_chunk_lines",
        "tool_output_limit",
        "max_candidates_in_prompt",
        # 语言范围：漏进白名单会让设置页保存直接 400（GUI 只能改白名单字段）
        "languages",
    ),
}

_HEADER = """# CodeSage 配置（可由 GUI 设置页写回）
# 优先级：环境变量 > 本文件 > 代码默认值
# API Key 不写在这里，也不能从网页写入——请放 .env 或 CODESAGE_LLM__API_KEY。
"""


def config_view(settings: Settings, store_path: str | None = None) -> dict[str, Any]:
    """白名单序列化：永不返回 api_key（只返回是否已配置）。"""
    budget = settings.budget
    view: dict[str, Any] = {
        "llm": {
            "base_url": settings.llm.base_url,
            "model_strong": settings.llm.model_strong,
            "model_fast": settings.llm.model_fast,
            "temperature": settings.llm.temperature,
            "max_output_tokens": settings.llm.max_output_tokens,
            "timeout": settings.llm.timeout,
            "configured": settings.llm_configured,
        },
        "budget": {
            "context_window": budget.context_window,
            "budget_window": budget.budget_window,
            "effective_budget_window": budget.effective_budget_window,
            "soft_ratio": budget.soft_ratio,
            "hard_ratio": budget.hard_ratio,
            "abort_ratio": budget.abort_ratio,
            "input_price_per_m": budget.input_price_per_m,
            "output_price_per_m": budget.output_price_per_m,
        },
        "review": {
            "max_turns": settings.review.max_turns,
            "active_window_turns": settings.review.active_window_turns,
            "max_file_lines": settings.review.max_file_lines,
            "languages": settings.review.languages,
            "ignore_dirs": settings.review.ignore_dirs,
        },
        "server": {
            "host": settings.server.host,
            "port": settings.server.port,
            "max_review_jobs": settings.server.max_review_jobs,
            "max_eval_jobs": settings.server.max_eval_jobs,
        },
        "config_file": str(active_config_path().resolve()),
        "editable": {section: list(fields) for section, fields in EDITABLE_FIELDS.items()},
        # 被环境变量覆盖的字段：写进 toml 也不会生效（环境变量优先级更高）
        "env_locked": _env_locked_fields(),
        # 按当前模型推导的标称窗口，供设置页做“换模型后建议值”提示
        "suggested_context_window": context_window_for(settings.llm.model_strong),
    }
    if store_path is not None:
        view["store_path"] = store_path
    return view


def save_config(settings: Settings, payload: dict[str, Any]) -> Settings:
    """把白名单字段写回 codesage.toml，并就地更新运行中的 settings。

    就地更新（而不是替换 app.state.settings）的原因：JobManager 和各处客户端
    都持有同一个 Settings 实例，替换对象会让它们继续读旧值。逐字段赋值后，
    进行中的任务下一轮就能看到新预算。
    """
    if not isinstance(payload, dict):
        raise ValueError("配置必须是 JSON 对象")

    unknown_sections = set(payload) - set(EDITABLE_FIELDS)
    if unknown_sections:
        raise ValueError(f"不支持修改的配置段：{', '.join(sorted(unknown_sections))}")

    _reject_env_locked(payload)

    merged = _merge_with_current(settings, payload)
    candidate = _build_candidate(settings, merged)
    _write_config_file(active_config_path(), merged)
    _apply_in_place(settings, candidate)
    return settings


# ------------------------------------------------------------------ 内部实现


def _reject_env_locked(payload: dict[str, Any]) -> None:
    """显式拒绝修改被环境变量/.env 锁定的字段。

    选择报错而不是静默忽略：静默忽略会让用户以为保存成功，重启后又看到旧值。
    """
    locked = _env_locked_fields()
    conflicts: list[str] = []
    for section, incoming in payload.items():
        if not isinstance(incoming, dict):
            continue
        for field in incoming:
            if field in locked.get(section, []):
                conflicts.append(f"CODESAGE_{section.upper()}__{field.upper()}")
    if conflicts:
        raise ValueError(
            "以下字段已被环境变量或 .env 固定，写入 toml 不会生效，请先移除它们："
            + ", ".join(sorted(conflicts))
        )


def _env_locked_fields() -> dict[str, list[str]]:
    """列出被环境变量或 .env 覆盖的白名单字段。

    这两者优先级都高于 codesage.toml，所以写进 toml 也不会生效——
    设置页必须把它们标出来，保存时也要拒绝，否则用户会遇到
    “改了没反应、重启又变回去”的困惑。
    """
    dotenv_keys = _dotenv_keys()
    locked: dict[str, list[str]] = {}
    for section, fields in EDITABLE_FIELDS.items():
        hits = [
            field
            for field in fields
            if f"CODESAGE_{section.upper()}__{field.upper()}" in os.environ
            or f"CODESAGE_{section.upper()}__{field.upper()}" in dotenv_keys
        ]
        if hits:
            locked[section] = hits
    return locked


def _dotenv_keys() -> set[str]:
    """读取 .env 的键名（不读值，也不打印，避免把密钥带进内存快照）。"""
    path = Path(".env")  # 与 SettingsConfigDict 的 env_file 保持一致
    if not path.is_file():
        return set()
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return set()
    keys: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if key:
            keys.add(key)
    return keys


def _merge_with_current(
    settings: Settings, payload: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """以当前生效配置为基线，只叠加提交上来的白名单字段。"""
    merged: dict[str, dict[str, Any]] = {}
    for section, allowed in EDITABLE_FIELDS.items():
        current = getattr(settings, section).model_dump()
        incoming = payload.get(section) or {}
        if not isinstance(incoming, dict):
            raise ValueError(f"{section} 段必须是对象")
        unknown = set(incoming) - set(allowed)
        if unknown:
            raise ValueError(
                f"{section} 段不支持修改：{', '.join(sorted(unknown))}"
            )
        merged[section] = {**{key: current[key] for key in allowed}, **incoming}
    return merged


def _build_candidate(settings: Settings, merged: dict[str, dict[str, Any]]) -> Settings:
    """用合并后的值构造一个新 Settings，借 pydantic 校验拦截非法输入。"""
    data = settings.model_dump()
    for section, values in merged.items():
        node = data.setdefault(section, {})
        node.update(values)
    try:
        return Settings(**data)
    except ValueError as exc:  # pydantic ValidationError 也是 ValueError
        raise ValueError(f"配置校验失败：{exc}") from exc


def _apply_in_place(target: Settings, candidate: Settings) -> None:
    """逐字段写回原对象，保持引用不变。"""
    for section in EDITABLE_FIELDS:
        node = getattr(target, section)
        for name, value in getattr(candidate, section).model_dump().items():
            setattr(node, name, value)


def _write_config_file(path: Path, merged: dict[str, dict[str, Any]]) -> None:
    """读旧文件 → 只覆盖白名单字段 → 原子替换。

    旧文件里未开放的字段（server 段、store_path 等）与顶层自定义键原样保留；
    注释无法保留，这点在 GUI 上有明确提示。
    """
    existing = _read_toml(path)
    for section, values in merged.items():
        node = existing.get(section)
        node = node if isinstance(node, dict) else {}
        node.update(values)
        existing[section] = node

    text = _HEADER + "\n" + _dump_toml(existing)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=".codesage-", suffix=".toml"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
            os.replace(tmp_name, path)
        except Exception:
            Path(tmp_name).unlink(missing_ok=True)
            raise
    except OSError as exc:
        raise ValueError(f"写入配置文件失败（{path}）：{exc}") from exc


def _read_toml(path: Path) -> dict[str, Any]:
    if tomllib is None or not path.is_file():
        return {}
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, ValueError):
        # 文件损坏时不让保存流程彻底失败：退化为“只写白名单字段”
        return {}
    return dict(data)


def _dump_toml(data: dict[str, Any]) -> str:
    """最小 TOML 序列化：顶层标量在前，表在后，只支持基础类型。"""
    scalars = {key: value for key, value in data.items() if not isinstance(value, dict)}
    tables = {key: value for key, value in data.items() if isinstance(value, dict)}

    lines = [f"{key} = {_format_value(value)}" for key, value in scalars.items()]
    blocks: list[str] = []
    for section, values in tables.items():
        block = [f"[{section}]"]
        block.extend(f"{key} = {_format_value(value)}" for key, value in values.items())
        blocks.append("\n".join(block))

    chunks = ["\n".join(lines)] if lines else []
    chunks.extend(blocks)
    return "\n\n".join(chunks).rstrip() + "\n"


def _format_value(value: Any) -> str:
    if isinstance(value, bool):  # 必须在 int 之前判断：bool 是 int 的子类
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_format_value(item) for item in value) + "]"
    raise ValueError(f"无法写入 TOML 的值类型：{type(value).__name__}")

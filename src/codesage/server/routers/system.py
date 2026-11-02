"""系统状态 / 元信息 / 配置（脱敏）/ 统计。"""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter

import codesage
from codesage.analyzers import default_analyzers
from codesage.server.deps import SettingsDep, StoreDep
from codesage.services.store_factory import resolve_store_path, store_available
from codesage.services.workspace_service import analyzer_readiness

router = APIRouter(tags=["system"])

PROFILES = [
    {"name": "full", "label": "全面审查", "hint": "质量 / Bug / 性能 / 安全 / 最佳实践"},
    {"name": "security", "label": "安全专项", "hint": "注入、密钥、命令执行"},
    {"name": "performance", "label": "性能专项", "hint": "复杂度、循环内拼接、资源使用"},
]


def static_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "static"


def _static_built() -> bool:
    return (static_dir() / "index.html").is_file()


@router.get("/health")
def health(settings: SettingsDep) -> dict:
    analyzers = [
        {"name": a.name, "available": bool(a.is_available())} for a in default_analyzers()
    ]
    return {
        "status": "ok",
        "version": codesage.__version__,
        "store_ok": store_available(settings),
        "store_path": str(resolve_store_path(settings)),
        "llm_configured": settings.llm_configured,
        "static_built": _static_built(),
        "git_available": shutil.which("git") is not None,
        "analyzers": analyzers,
    }


@router.get("/meta")
def meta() -> dict:
    return {
        "version": codesage.__version__,
        "profiles": PROFILES,
        "severities": [
            {"name": "critical", "label": "P0", "zh": "严重"},
            {"name": "major", "label": "P1", "zh": "重要"},
            {"name": "minor", "label": "P2", "zh": "次要"},
            {"name": "info", "label": "P3", "zh": "提示"},
        ],
        "categories": [
            {"name": "security", "zh": "安全"},
            {"name": "bug", "zh": "潜在 Bug"},
            {"name": "performance", "zh": "性能"},
            {"name": "quality", "zh": "代码质量"},
            {"name": "best_practice", "zh": "最佳实践"},
        ],
        "sources": [
            {"name": "ast_rule", "zh": "自研规则"},
            {"name": "ruff", "zh": "Ruff"},
            {"name": "bandit", "zh": "Bandit"},
            {"name": "radon", "zh": "radon"},
            {"name": "llm", "zh": "LLM 推理"},
        ],
        "verdicts": [
            {"name": "wrong", "zh": "误报"},
            {"name": "right", "zh": "确认"},
            {"name": "missed", "zh": "漏报"},
        ],
        "analyzer_ready": analyzer_readiness(),
    }


@router.get("/config")
def config(settings: SettingsDep) -> dict:
    """白名单序列化：永不返回 api_key（只返回是否已配置）。"""
    return {
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
            "context_window": settings.budget.context_window,
            "soft_ratio": settings.budget.soft_ratio,
            "hard_ratio": settings.budget.hard_ratio,
            "abort_ratio": settings.budget.abort_ratio,
        },
        "review": {
            "max_turns": settings.review.max_turns,
            "active_window_turns": settings.review.active_window_turns,
            "max_file_lines": settings.review.max_file_lines,
            "ignore_dirs": settings.review.ignore_dirs,
        },
        "server": {
            "host": settings.server.host,
            "port": settings.server.port,
            "max_review_jobs": settings.server.max_review_jobs,
            "max_eval_jobs": settings.server.max_eval_jobs,
        },
        "store_path": str(resolve_store_path(settings)),
    }


@router.get("/stats")
def stats(store: StoreDep) -> dict:
    return store.stats_overview()

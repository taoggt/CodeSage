"""系统状态 / 元信息 / 配置（脱敏）/ 统计。"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter

import codesage
from codesage.analyzers import default_analyzers, default_rule_engines
from codesage.core.languages import all_specs
from codesage.server.deps import SettingsDep, StoreDep
from codesage.services.config_service import config_view, save_config
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
        {"name": engine.name, "available": bool(engine.is_available()), "kind": "rule"}
        for engine in default_rule_engines()
    ]
    analyzers += [
        {"name": a.name, "available": bool(a.is_available()), "kind": "tool"}
        for a in default_analyzers()
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
            {"name": "java_rule", "zh": "Java 规则"},
            {"name": "ruff", "zh": "Ruff"},
            {"name": "bandit", "zh": "Bandit"},
            {"name": "radon", "zh": "radon"},
            {"name": "llm", "zh": "LLM 推理"},
        ],
        # 语言清单由注册表生成：新增语言不需要改这个文件
        "languages": [
            {
                "code": spec.code,
                "display": spec.display,
                "extensions": list(spec.extensions),
                "fence": spec.markdown_fence,
            }
            for spec in all_specs()
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
    return config_view(settings, str(resolve_store_path(settings)))


@router.put("/config")
def update_config(payload: dict[str, Any], settings: SettingsDep) -> dict:
    """保存设置页提交的白名单字段：写回 codesage.toml 并热更新运行中的配置。

    只允许改 llm / budget / review 三段的白名单字段；
    密钥与 server 段（改端口、并发要重启）不在其中。
    """
    save_config(settings, payload)
    return config_view(settings, str(resolve_store_path(settings)))


@router.get("/stats")
def stats(store: StoreDep) -> dict:
    return store.stats_overview()

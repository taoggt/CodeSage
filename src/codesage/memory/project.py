"""项目级记忆：项目画像（供 system prompt 的“项目背景”段）。

零 Token 的确定性分析：读取 import 语句识别技术栈，
扫描目录结构生成概览——让模型知道自己在审查什么项目。
"""

from __future__ import annotations

import ast
from pathlib import Path

# 常见框架/库的识别线索（顶层包名 → 展示名）
FRAMEWORK_HINTS = {
    "fastapi": "FastAPI",
    "flask": "Flask",
    "django": "Django",
    "starlette": "Starlette",
    "sqlalchemy": "SQLAlchemy",
    "pydantic": "Pydantic",
    "pytest": "pytest",
    "unittest": "unittest",
    "pandas": "pandas",
    "numpy": "NumPy",
    "requests": "requests",
    "httpx": "httpx",
    "celery": "Celery",
    "redis": "Redis",
    "pymongo": "PyMongo",
    "typer": "Typer",
    "click": "Click",
    "asyncio": "asyncio",
    "aiohttp": "aiohttp",
    "torch": "PyTorch",
    "transformers": "Transformers",
}

MAX_FILES_TO_SCAN = 60  # 项目画像只扫描前 N 个文件（成本控制）


def detect_dependencies(files: list[Path], limit: int = MAX_FILES_TO_SCAN) -> list[str]:
    """扫描 import 语句识别技术栈。"""
    found: set[str] = set()
    for path in files[:limit]:
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top in FRAMEWORK_HINTS:
                        found.add(FRAMEWORK_HINTS[top])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top = node.module.split(".")[0]
                    if top in FRAMEWORK_HINTS:
                        found.add(FRAMEWORK_HINTS[top])
    return sorted(found)


def summarize_structure(root: Path, files: list[Path]) -> str:
    """生成顶层目录结构概览（最多展示 12 个条目）。"""
    rel_dirs: set[str] = set()
    for path in files:
        try:
            rel = path.resolve().relative_to(root.resolve())
        except ValueError:
            continue
        if len(rel.parts) > 1:
            rel_dirs.add(rel.parts[0] + "/")
        else:
            rel_dirs.add("./")
    dirs = sorted(rel_dirs)[:12]
    return ", ".join(dirs)


def build_workspace_summary(root: Path, files: list[Path]) -> str:
    """组装项目背景文本（注入 system prompt）。"""
    if not files:
        return ""

    total_lines = 0
    for path in files[:MAX_FILES_TO_SCAN]:
        try:
            total_lines += path.read_text(
                encoding="utf-8", errors="replace"
            ).count("\n")
        except OSError:
            continue

    deps = detect_dependencies(files)
    structure = summarize_structure(root, files)

    lines = [
        f"- 语言：Python（{len(files)} 个文件，约 {total_lines} 行）",
        f"- 顶层结构：{structure or './'}",
    ]
    if deps:
        lines.append(f"- 疑似技术栈：{', '.join(deps)}")
    lines.append(
        "- 审查时请结合项目技术栈给出符合该生态惯例的建议"
        "（如 Web 项目关注请求处理与注入、数据项目关注向量化与内存）。"
    )
    return "\n".join(lines)

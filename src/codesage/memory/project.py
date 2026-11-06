"""项目级记忆：项目画像（供 system prompt 的"项目背景"段）。

零 Token 的确定性分析：读取 import 语句识别技术栈，
扫描目录结构生成概览——让模型知道自己在审查什么项目。

多语言改造：技术栈线索现在来自 `core.languages.LanguageSpec.framework_hints`
（按语言分组），识别方式按语言分派——Python 走 `ast` 读 import，Java 走
`import x.y.Z;` 正则。这里不再维护第二份框架清单，避免两处漂移。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from codesage.core.languages import LANGUAGE_SPECS, Language, language_for_path

MAX_FILES_TO_SCAN = 60  # 项目画像只扫描前 N 个文件（成本控制）

_JAVA_IMPORT = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+)\s*;", re.MULTILINE)


def _hints_for(language: Language) -> dict[str, str]:
    spec = LANGUAGE_SPECS.get(language)
    return dict(spec.framework_hints) if spec else {}


def _python_dependencies(source: str, found: set[str]) -> None:
    """Python：AST 读 import（比正则准，能处理括号续行与别名）。"""
    hints = _hints_for(Language.PYTHON)
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in hints:
                    found.add(hints[top])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top = node.module.split(".")[0]
                if top in hints:
                    found.add(hints[top])


def _java_dependencies(source: str, found: set[str]) -> None:
    """Java：正则读 import 声明。

    为什么不用 tree-sitter：项目画像只扫前 60 个文件，正则足够且零依赖——
    这段代码在没装 [java] extra 的环境里也要能工作（否则 prompt 组装会崩）。
    """
    hints = _hints_for(Language.JAVA)
    for match in _JAVA_IMPORT.finditer(source):
        qualified = match.group(1)
        for prefix, display in hints.items():
            if qualified == prefix or qualified.startswith(prefix + "."):
                found.add(display)


def detect_dependencies(files: list[Path], limit: int = MAX_FILES_TO_SCAN) -> list[str]:
    """扫描 import 语句识别技术栈（按语言分派）。"""
    found: set[str] = set()
    for path in files[:limit]:
        language = language_for_path(path)
        if language is None:
            continue
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if language is Language.JAVA:
            _java_dependencies(source, found)
        else:
            _python_dependencies(source, found)
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


def _describe_languages(files: list[Path]) -> str:
    """按文件后缀统计语言构成，如 "Java 12 个、Python 3 个"。"""
    counts: dict[Language, int] = {}
    for path in files:
        language = language_for_path(path)
        if language is not None:
            counts[language] = counts.get(language, 0) + 1
    if not counts:
        return "未知"
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0].value))
    if len(ordered) == 1:
        return LANGUAGE_SPECS[ordered[0][0]].display
    return "、".join(
        f"{LANGUAGE_SPECS[language].display} {count} 个" for language, count in ordered
    )


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
        f"- 语言：{_describe_languages(files)}（{len(files)} 个文件，约 {total_lines} 行）",
        f"- 顶层结构：{structure or './'}",
    ]
    if deps:
        lines.append(f"- 疑似技术栈：{', '.join(deps)}")
    lines.append(
        "- 审查时请结合项目技术栈给出符合该生态惯例的建议"
        "（如 Web 项目关注请求处理与注入、数据项目关注向量化与内存）。"
    )
    return "\n".join(lines)

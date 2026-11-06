"""search_code 工具：正则搜索（零 Token 定位手段，结果条数有上限）。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from codesage.agent.registry import tool
from codesage.core.languages import all_extensions
from codesage.ingest.filters import DEFAULT_EXCLUDES


def make_search_code_tool(
    workspace: Path,
    max_results: int = 100,
) -> Callable[..., str]:
    root = Path(workspace).resolve()
    excluded = set(DEFAULT_EXCLUDES)

    @tool(name="search_code")
    def search_code(pattern: str, file_glob: str = "") -> str:
        """在工作区源码中做正则搜索，返回“文件:行号: 内容”列表。

        Args:
            pattern: 正则表达式（如 "def get_" / "executeQuery" / "@Transactional"）
            file_glob: 文件名过滤；留空表示"所有已注册语言的源码文件"
                （如 "*.java" 只搜 Java）
        """
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            return f"ERROR: 正则表达式无效：{exc}"

        results: list[str] = []
        truncated = False
        glob = str(file_glob).strip()
        try:
            if glob in {"", "*"}:
                # 默认范围 = 已注册语言的源码后缀。写死 "*.py" 会让 Java 仓库里
                # 的搜索永远返回空——模型于是以为"代码里没有这个符号"。
                extensions = set(all_extensions())
                paths = sorted(
                    path
                    for path in root.rglob("*")
                    if path.is_file() and path.suffix.lower() in extensions
                )
            else:
                paths = sorted(root.rglob(glob))
        except OSError as exc:
            return f"ERROR: 目录遍历失败：{exc}"

        for path in paths:
            if not path.is_file():
                continue
            try:
                rel_parts = path.relative_to(root).parts
            except ValueError:
                continue
            if any(part in excluded for part in rel_parts):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rel = Path(*rel_parts).as_posix()
            for idx, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    results.append(f"{rel}:{idx}: {line.strip()[:160]}")
                    if len(results) >= max_results:
                        truncated = True
                        break
            if truncated:
                break

        if not results:
            return "（无匹配）"
        suffix = f"\n...（已达上限 {max_results} 条，请收窄正则）" if truncated else ""
        return "\n".join(results) + suffix

    return search_code

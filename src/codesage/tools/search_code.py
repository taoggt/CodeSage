"""search_code 工具：正则搜索（零 Token 定位手段，结果条数有上限）。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from codesage.agent.registry import tool
from codesage.ingest.filters import DEFAULT_EXCLUDES


def make_search_code_tool(
    workspace: Path,
    max_results: int = 100,
) -> Callable[..., str]:
    root = Path(workspace).resolve()
    excluded = set(DEFAULT_EXCLUDES)

    @tool(name="search_code")
    def search_code(pattern: str, file_glob: str = "*.py") -> str:
        """在工作区源码中做正则搜索，返回“文件:行号: 内容”列表。

        Args:
            pattern: Python 正则表达式（如 "def get_" 或 "subprocess\\."）
            file_glob: 文件名过滤（默认 "*.py"）
        """
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            return f"ERROR: 正则表达式无效：{exc}"

        results: list[str] = []
        truncated = False
        try:
            paths = sorted(root.rglob(file_glob))
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

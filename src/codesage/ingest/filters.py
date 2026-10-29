"""文件筛选：路径级判断（是否属于审查范围）。

判断优先级：排除目录 > 扩展名 > .gitignore > 大小（大小由 scanner 读文件后判断）。
.gitignore 采用简易匹配（fnmatch + 路径前缀），覆盖常见用法；
完整 gitignore 语义（否定规则、** 等）不在 v1 范围。
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

DEFAULT_EXCLUDES = [
    ".git", "__pycache__", ".venv", "venv", "node_modules",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "build", "dist", ".eggs", ".codesage",
]


class FileFilter:
    """决定文件是否进入审查范围。"""

    def __init__(
        self,
        root: Path,
        excludes: list[str] | None = None,
        max_file_lines: int = 2000,
    ) -> None:
        self.root = Path(root).resolve()
        self.excludes = set(excludes or DEFAULT_EXCLUDES)
        self.max_file_lines = max_file_lines
        self._gitignore_patterns = self._load_gitignore()

    def _load_gitignore(self) -> list[str]:
        patterns: list[str] = []
        gitignore = self.root / ".gitignore"
        if not gitignore.exists():
            return patterns
        try:
            text = gitignore.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return patterns
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("!"):
                continue
            patterns.append(line.rstrip("/"))
        return patterns

    def _matches_gitignore(self, rel_posix: str) -> bool:
        for pattern in self._gitignore_patterns:
            if pattern.startswith("/"):
                pattern = pattern[1:]
            if fnmatch.fnmatch(rel_posix, pattern):
                return True
            # 目录前缀：如 "docs" 匹配 "docs/xx.py"
            if "/" not in pattern and fnmatch.fnmatch(rel_posix, f"{pattern}/*"):
                return True
            if fnmatch.fnmatch(rel_posix, f"*/{pattern}"):
                return True
        return False

    def is_excluded_dir(self, name: str) -> bool:
        """遍历时的目录剪枝判断。"""
        return name in self.excludes or name.endswith(".egg-info")

    def should_include(self, path: Path) -> tuple[bool, str]:
        """路径级筛选。返回 (是否纳入, 跳过原因)。"""
        try:
            rel = path.resolve().relative_to(self.root)
        except ValueError:
            return False, "不在工作区目录内"

        # 任意层级的排除目录
        for part in rel.parts[:-1]:
            if self.is_excluded_dir(part):
                return False, f"排除目录：{part}"

        if path.suffix != ".py":
            return False, "非 Python 文件（v1 仅支持 Python）"

        rel_posix = rel.as_posix()
        if self._matches_gitignore(rel_posix):
            return False, "匹配 .gitignore 规则"

        return True, ""

"""文件筛选：路径级判断（是否属于审查范围）。

判断优先级：排除目录 > 扩展名 > .gitignore > 大小（大小由 scanner 读文件后判断）。
.gitignore 采用简易匹配（fnmatch + 路径前缀），覆盖常见用法；
完整 gitignore 语义（否定规则、** 等）不在 v1 范围。

多语言改造（v2）：跳过原因从裸中文字符串升级为 `SkipReason` 枚举。
为什么必须改——旧代码 `scanner.py` 用**面向用户的文案**当控制流条件：

    if reason != "非 Python 文件（v1 仅支持 Python）":

这种写法一改文案就静默失效：混合仓库里 .js/.md/.png 会全部涌进
skipped 列表（Java 项目里非代码文件数量级远大于 Python 项目，问题被放大）。
枚举把"为什么跳过"与"怎么展示"彻底分开：前者是代码判断，后者是 `detail`。
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable

from codesage.core.languages import (
    Language,
    all_languages,
    describe_languages,
    extensions_label,
    language_spec_for_path,
)

DEFAULT_EXCLUDES = [
    ".git", "__pycache__", ".venv", "venv", "node_modules",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "build", "dist", ".eggs", ".codesage",
]


class SkipReason(str, Enum):
    """跳过原因（结构化，可计数、可判断，不参与文案比较）。"""

    OUTSIDE_ROOT = "outside_root"
    EXCLUDED_DIR = "excluded_dir"
    UNSUPPORTED_LANGUAGE = "unsupported_language"  # 唯一"不逐条记录"的原因
    GITIGNORE = "gitignore"
    LARGE_FILE = "large_file"
    EMPTY_FILE = "empty_file"


@dataclass(frozen=True)
class SkipVerdict:
    """一次筛选结论：为什么跳过 + 给人看的说明。"""

    reason: SkipReason
    detail: str


class FileFilter:
    """决定文件是否进入审查范围。

    `languages` 是"本次审查允许的语言集合"：默认只含 Python，与 v1 行为等价；
    pipeline 在 auto 模式下传入全部已注册语言，显式 `--language java` 时只传 Java。
    """

    def __init__(
        self,
        root: Path,
        excludes: list[str] | None = None,
        max_file_lines: int = 2000,
        languages: tuple[Language, ...] | None = None,
        extra_excludes: Iterable[str] = (),
    ) -> None:
        self.root = Path(root).resolve()
        merged = list(excludes) if excludes is not None else list(DEFAULT_EXCLUDES)
        for name in extra_excludes:
            if name not in merged:
                merged.append(name)
        self.excludes = set(merged)
        self.max_file_lines = max_file_lines
        self.languages: tuple[Language, ...] = (
            tuple(languages) if languages is not None else (Language.PYTHON,)
        )
        self._gitignore_patterns = self._load_gitignore()

    # ------------------------------------------------------------ 内部
    @property
    def languages_label(self) -> str:
        return describe_languages(self.languages)

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

    def max_lines_for(self, path: Path) -> int:
        """该文件适用的行数上限（语言可覆盖全局阈值）。

        Java 的领域服务类常见 2000+ 行，沿用 Python 阈值会把它们静默跳过，
        用户却以为"审过了"——这是比误报更危险的失效。
        """
        spec = language_spec_for_path(path)
        if (
            spec is not None
            and spec.language in self.languages
            and spec.max_file_lines is not None
        ):
            return spec.max_file_lines
        return self.max_file_lines

    # ------------------------------------------------------------ 对外
    def classify(self, path: Path) -> SkipVerdict | None:
        """路径级筛选。返回 None 表示纳入；否则给出结构化跳过结论。"""
        try:
            rel = path.resolve().relative_to(self.root)
        except ValueError:
            return SkipVerdict(SkipReason.OUTSIDE_ROOT, "不在工作区目录内")

        # 任意层级的排除目录
        for part in rel.parts[:-1]:
            if self.is_excluded_dir(part):
                return SkipVerdict(SkipReason.EXCLUDED_DIR, f"排除目录：{part}")

        spec = language_spec_for_path(path)
        if spec is None or spec.language not in self.languages:
            return SkipVerdict(
                SkipReason.UNSUPPORTED_LANGUAGE,
                f"非受支持的语言（本次范围：{self.languages_label}，"
                f"支持 {extensions_label(self.languages)}）",
            )

        rel_posix = rel.as_posix()
        if self._matches_gitignore(rel_posix):
            return SkipVerdict(SkipReason.GITIGNORE, "匹配 .gitignore 规则")

        return None

    def should_include(self, path: Path) -> tuple[bool, str]:
        """兼容外壳：保留 (是否纳入, 跳过原因) 二元组签名。

        历史调用点（scanner / tests / 外部脚本）依赖这个形状，而 pipeline 内部
        需要结构化原因——所以 classify() 是主接口，本方法只为兼容而存在。
        """
        verdict = self.classify(path)
        if verdict is None:
            return True, ""
        return False, verdict.detail


def default_excludes_for(languages: Iterable[Language]) -> list[str]:
    """按语言合并排除目录（供 pipeline 组装 FileFilter.extra_excludes）。"""
    from codesage.core.languages import LANGUAGE_SPECS

    merged: list[str] = []
    for language in languages:
        spec = LANGUAGE_SPECS.get(language)
        if spec is None:
            continue
        for name in spec.extra_excludes:
            if name not in merged:
                merged.append(name)
    return merged


__all__ = [
    "DEFAULT_EXCLUDES",
    "FileFilter",
    "SkipReason",
    "SkipVerdict",
    "all_languages",
    "default_excludes_for",
]

"""git diff 解析：定位变更行范围（用于 --changed 增量审查模式）。

实现方式：解析 `git diff HEAD -U0` 的 hunk 头（@@ -a,b +c,d @@），
只取新增侧的行号范围。这是零依赖实现（不引入 GitPython）。

用途：增量审查——只审查本次改动涉及的行区间，
既省 Token 又符合开发者的真实工作流（对齐 CI / pre-commit 场景）。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


@dataclass
class HunkRange:
    """新增侧的行号区间（1-based，含两端）。"""

    start: int
    end: int


def is_git_repo(path: Path) -> bool:
    """判断路径是否位于 git 仓库内。"""
    if shutil.which("git") is None:
        return False
    try:
        proc = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return proc.returncode == 0 and proc.stdout.strip() == "true"
    except (OSError, subprocess.TimeoutExpired):
        return False


def changed_line_ranges(repo: Path, base: str = "HEAD") -> dict[str, list[HunkRange]]:
    """返回 {相对路径: [变更行区间]}。

    对比范围：工作区（含暂存） vs base（默认 HEAD）。
    失败时返回空 dict（调用方决定回退到全量审查）。
    """
    if not is_git_repo(repo):
        return {}

    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "diff", base, "-U0", "--no-color", "--no-ext-diff"],
            capture_output=True,
            text=True,
            timeout=60,
            encoding="utf-8",
            errors="ignore",
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}

    if proc.returncode != 0:
        return {}

    result: dict[str, list[HunkRange]] = {}
    current: str | None = None

    for line in proc.stdout.splitlines():
        if line.startswith("+++ "):
            # "+++ b/path/to/file.py" 或 "+++ /dev/null"
            target = line[4:].strip()
            if target.startswith("b/"):
                current = target[2:]
            else:
                current = None
            if current and current not in result:
                result[current] = []
            continue
        if line.startswith("+++") or line.startswith("---"):
            continue
        if current is None:
            continue
        m = HUNK_RE.match(line)
        if m:
            start = int(m.group(1))
            length = int(m.group(2)) if m.group(2) else 1
            if length <= 0:
                continue
            result[current].append(HunkRange(start=start, end=start + length - 1))

    # 清理无变更的文件
    return {k: v for k, v in result.items() if v}


def expand_ranges(ranges: list[HunkRange], padding: int = 0) -> list[tuple[int, int]]:
    """把区间列表转成 (start, end) 元组，可选向外扩展 padding 行。"""
    out: list[tuple[int, int]] = []
    for r in ranges:
        out.append((max(1, r.start - padding), r.end + padding))
    return out

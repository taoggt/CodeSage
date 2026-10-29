"""目录扫描：产出待审查文件清单（含跳过原因，透明可解释）。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from codesage.ingest.filters import FileFilter


@dataclass
class ScanResult:
    """扫描结果。"""

    files: list[Path] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (相对路径, 原因)

    @property
    def total_candidates(self) -> int:
        return len(self.files) + len(self.skipped)


def count_lines(path: Path) -> int:
    """快速统计行数（二进制读，不计编码问题）。"""
    try:
        with open(path, "rb") as f:
            return f.read().count(b"\n") + 1
    except OSError:
        return 0


def scan(root: Path, file_filter: FileFilter) -> ScanResult:
    """遍历 root，应用筛选规则，返回文件清单。

    使用 os.walk(topdown=True) 直接剪枝排除目录，避免进入 .venv 等深目录。
    """
    root = Path(root).resolve()
    result = ScanResult()

    # 单文件场景
    if root.is_file():
        ok, reason = file_filter.should_include(root)
        if ok:
            result.files.append(root)
        else:
            result.skipped.append((root.name, reason))
        return result

    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        # 剪枝：原地修改 dirnames 阻止递归
        dirnames[:] = [d for d in dirnames if not file_filter.is_excluded_dir(d)]
        for name in sorted(filenames):
            path = Path(dirpath) / name
            ok, reason = file_filter.should_include(path)
            if not ok:
                if reason != "非 Python 文件（v1 仅支持 Python）":
                    # 非 Python 文件太多，不逐条记录，只记录有意义的跳过
                    result.skipped.append((_rel(path, root), reason))
                continue
            lines = count_lines(path)
            if lines > file_filter.max_file_lines:
                result.skipped.append(
                    (_rel(path, root), f"大文件预过滤（{lines} 行 > {file_filter.max_file_lines}）")
                )
                continue
            if lines <= 1:
                result.skipped.append((_rel(path, root), "空文件或单行文件"))
                continue
            result.files.append(path)

    result.files.sort()
    return result


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return str(path)

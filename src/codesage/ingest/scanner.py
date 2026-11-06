"""目录扫描：产出待审查文件清单（含跳过原因，透明可解释）。

v2 变更：跳过原因不再用中文字符串做控制流，改由 `SkipReason` 枚举判断。
`skipped` 的形状保持 `list[(相对路径, 人话原因)]` 不变（报告/GUI 都消费它），
另外新增两个计数视图：
- `skip_counts`：按 SkipReason.value 计数（含"不逐条记录"的那一类）
- `language_counts`：按语言统计**已纳入**的文件数（auto 探测的真正依据）
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from codesage.core.languages import language_for_path
from codesage.ingest.filters import FileFilter, SkipReason

# 只有这一类跳过不逐条记录：混合仓库里非目标语言文件数量级过大，
# 逐条塞进 skipped 会让报告体积和 GUI「已跳过」面板爆炸。
QUIET_SKIP_REASONS: frozenset[SkipReason] = frozenset({SkipReason.UNSUPPORTED_LANGUAGE})


@dataclass
class ScanResult:
    """扫描结果。"""

    files: list[Path] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (相对路径, 原因)
    skip_counts: dict[str, int] = field(default_factory=dict)     # SkipReason.value → 数量
    language_counts: dict[str, int] = field(default_factory=dict) # 语言 → 已纳入文件数

    @property
    def total_candidates(self) -> int:
        return len(self.files) + len(self.skipped)

    def record_skip(self, path_label: str, reason: SkipReason, detail: str) -> None:
        self.skip_counts[reason.value] = self.skip_counts.get(reason.value, 0) + 1
        if reason not in QUIET_SKIP_REASONS:
            self.skipped.append((path_label, detail))

    def record_file(self, path: Path) -> None:
        self.files.append(path)
        language = language_for_path(path)
        if language is not None:
            code = language.value
            self.language_counts[code] = self.language_counts.get(code, 0) + 1


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
        verdict = file_filter.classify(root)
        if verdict is None:
            result.record_file(root)
        else:
            result.record_skip(root.name, verdict.reason, verdict.detail)
        return result

    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        # 剪枝：原地修改 dirnames 阻止递归
        dirnames[:] = [d for d in dirnames if not file_filter.is_excluded_dir(d)]
        for name in sorted(filenames):
            path = Path(dirpath) / name
            verdict = file_filter.classify(path)
            if verdict is not None:
                result.record_skip(_rel(path, root), verdict.reason, verdict.detail)
                continue

            lines = count_lines(path)
            limit = file_filter.max_lines_for(path)
            if lines > limit:
                result.record_skip(
                    _rel(path, root),
                    SkipReason.LARGE_FILE,
                    f"大文件预过滤（{lines} 行 > {limit}）",
                )
                continue
            if lines <= 1:
                result.record_skip(
                    _rel(path, root), SkipReason.EMPTY_FILE, "空文件或单行文件"
                )
                continue
            result.record_file(path)

    result.files.sort()
    return result


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return str(path)

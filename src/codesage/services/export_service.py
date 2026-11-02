"""导出服务：把一次审查导出为 Markdown / JSON。

复用 `cli.exporter`，保证 GUI 下载的文件与 `codesage review --format` 逐字一致。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from codesage.cli.exporter import to_json, to_markdown
from codesage.core.models import ReviewReport

MEDIA_TYPES = {
    "md": "text/markdown; charset=utf-8",
    "markdown": "text/markdown; charset=utf-8",
    "json": "application/json; charset=utf-8",
}


def render(payload: dict[str, Any], fmt: str) -> tuple[str, str, str]:
    """返回 (正文, 文件名, Content-Type)。"""
    normalized = "json" if fmt.lower() == "json" else "md"
    report = ReviewReport.from_dict(payload)
    body = to_json(report) if normalized == "json" else to_markdown(report)
    stem = Path(report.target).stem or "codesage_report"
    filename = f"{stem}.codesage.{ 'json' if normalized == 'json' else 'md'}"
    return body, filename, MEDIA_TYPES[normalized]

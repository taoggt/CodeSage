"""评测数据集：YAML 案例加载。

案例格式（见 examples/eval_dataset/）：
    id: case_001
    category: security
    description: SQL 拼接注入
    snippet: snippets/case_001.py
    ground_truth:
      line_range: [8, 9]
      severity: major
      keywords: ["execute", "select", "{"]
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from codesage.core.errors import HarnessError


@dataclass
class EvalCase:
    """一个评测案例。"""

    id: str
    category: str
    description: str
    snippet_path: Path        # 绝对路径
    line_start: int
    line_end: int
    keywords: list[str]
    severity: str
    dataset_dir: Path

    @property
    def snippet_name(self) -> str:
        return self.snippet_path.name


def load_dataset(dataset_dir: Path) -> list[EvalCase]:
    """加载目录下全部 *.yaml 案例。"""
    dataset_dir = Path(dataset_dir)
    if not dataset_dir.is_dir():
        raise HarnessError(f"数据集目录不存在：{dataset_dir}")

    cases: list[EvalCase] = []
    for path in sorted(dataset_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise HarnessError(f"案例解析失败：{path.name}：{exc}") from exc

        snippet_rel = str(data.get("snippet", "")).strip()
        if not snippet_rel:
            raise HarnessError(f"案例缺少 snippet 字段：{path.name}")
        snippet_path = (dataset_dir / snippet_rel).resolve()
        if not snippet_path.is_file():
            raise HarnessError(f"案例的代码片段不存在：{path.name} → {snippet_rel}")

        gt = data.get("ground_truth") or {}
        line_range = gt.get("line_range") or [1, 1]
        cases.append(
            EvalCase(
                id=str(data.get("id", path.stem)),
                category=str(data.get("category", "unknown")),
                description=str(data.get("description", "")),
                snippet_path=snippet_path,
                line_start=int(line_range[0]),
                line_end=int(line_range[1]) if len(line_range) > 1 else int(line_range[0]),
                keywords=[str(k) for k in (gt.get("keywords") or [])],
                severity=str(gt.get("severity", "major")),
                dataset_dir=dataset_dir,
            )
        )
    if not cases:
        raise HarnessError(f"数据集目录中没有 .yaml 案例：{dataset_dir}")
    return cases

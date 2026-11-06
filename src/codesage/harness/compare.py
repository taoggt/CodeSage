"""A/B 对比：两次评测的回归报告。

典型用途：
- prompt 修改前后（结合 optim/prompts 的版本号）
- 错题本注入前后（验证自优化是否真的有效）
- 不同模型对比（deepseek-flash vs deepseek-v4-pro 的成本-质量曲线）
- 压缩阈值参数对比（60/80 vs 70/90 的取舍数据）

对比原则（面试要点）：同一数据集、同一模型、同一参数——
只变一个自变量，否则对比结果无法归因。
"""

from __future__ import annotations

import json
from pathlib import Path

from codesage.core.errors import HarnessError


def load_summary(results_dir: Path, tag: str) -> dict:
    path = Path(results_dir) / f"{tag}_summary.json"
    if not path.is_file():
        raise HarnessError(f"找不到评测结果：{path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _case_map(summary: dict) -> dict[str, dict]:
    return {str(c.get("case_id")): c for c in summary.get("cases_detail", [])}


def compare(results_dir: Path, tag_a: str, tag_b: str) -> dict:
    """对比两次评测。

    返回：
    - metrics: 各指标 A → B 的对比（delta）
    - regressions: 从检出变漏报的案例（回归）
    - improvements: 从漏报变检出的案例（改善）
    """
    a = load_summary(results_dir, tag_a)
    b = load_summary(results_dir, tag_b)

    metric_keys = ["cases", "detected", "recall", "precision", "f1",
                   "reported_total", "matched_total", "total_tokens",
                   "total_cost_yuan", "avg_cost_yuan"]
    metric_deltas: list[dict] = []
    for key in metric_keys:
        if key in ("cases",):
            continue
        va = a.get(key, 0)
        vb = b.get(key, 0)
        delta = (vb - va) if isinstance(va, (int, float)) else None
        metric_deltas.append(
            {"metric": key, "a": va, "b": vb,
             "delta": round(delta, 4) if delta is not None else None}
        )

    cases_a = _case_map(a)
    cases_b = _case_map(b)
    regressions: list[dict] = []
    improvements: list[dict] = []
    for case_id in sorted(set(cases_a) & set(cases_b)):
        ca, cb = cases_a[case_id], cases_b[case_id]
        if ca.get("detected") and not cb.get("detected"):
            regressions.append({"case_id": case_id, "detail": cb})
        elif not ca.get("detected") and cb.get("detected"):
            improvements.append({"case_id": case_id, "detail": cb})

    return {
        "tag_a": tag_a,
        "tag_b": tag_b,
        "metrics": metric_deltas,
        "regressions": regressions,
        "improvements": improvements,
    }

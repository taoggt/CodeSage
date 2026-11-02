"""评测运行器：对数据集批量执行真实审查链路并落盘结果。

关键设计：
- 走真实链路（调用 pipeline.run_review），而不是直接调分析器——
  保证评测结果反映用户实际使用时的行为（harness 不 import agent 内部模块）；
- mock 模式（--no-llm）只跑确定性层：无 API Key 也能验证指标管线、
  为 CI 提供稳定的回归基线；
- 结果落盘 JSONL（逐案例）+ summary JSON（聚合指标），
  供 compare 做 A/B 回归。
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from codesage.core.cancel import CancelToken
from codesage.core.config import Settings
from codesage.core.errors import HarnessError
from codesage.harness.dataset import load_dataset
from codesage.harness.metrics import aggregate, evaluate_case


@dataclass
class EvalOptions:
    """评测选项。"""

    mock: bool = False       # True = 仅确定性层（等价 review --no-llm）
    sample: int | None = None  # 只跑前 N 个案例（开发期快速迭代）
    profile: str = "full"


class EvalRunner:
    """批量评测。"""

    def __init__(
        self,
        settings: Settings,
        dataset_dir: Path,
        results_dir: Path,
        options: EvalOptions | None = None,
        on_progress: Callable[[str, int, int, dict], None] | None = None,
        cancel_token: CancelToken | None = None,
    ) -> None:
        self._settings = settings
        self._dataset_dir = Path(dataset_dir)
        self._results_dir = Path(results_dir)
        self._options = options or EvalOptions()
        self._on_progress = on_progress
        self._cancel_token = cancel_token

    def run(self, tag: str) -> dict:
        """执行评测，返回 summary，并写盘。"""
        from codesage.pipeline import ReviewOptions, run_review

        cases = load_dataset(self._dataset_dir)
        if self._options.sample:
            cases = cases[: self._options.sample]

        self._results_dir.mkdir(parents=True, exist_ok=True)
        per_case: list[dict] = []
        total_tokens = 0
        total_cost = 0.0
        cancelled = False
        started = time.perf_counter()

        for idx, case in enumerate(cases, start=1):
            if self._cancel_token is not None and self._cancel_token.cancelled:
                cancelled = True
                break
            case_result = self._run_single_case(case, run_review, ReviewOptions)
            per_case.append(case_result)
            total_tokens += int(case_result.get("tokens", 0))
            total_cost += float(case_result.get("cost_yuan", 0.0))
            if self._on_progress is not None:
                self._on_progress(case.id, idx, len(cases), case_result)

        summary = aggregate(per_case)
        completed = len(per_case)
        summary.update(
            {
                "tag": tag,
                "mock": self._options.mock,
                "profile": self._options.profile,
                "duration_sec": round(time.perf_counter() - started, 2),
                "total_tokens": total_tokens,
                "total_cost_yuan": round(total_cost, 4),
                "avg_cost_yuan": round(total_cost / completed, 4) if completed else 0.0,
                "cases_detail": per_case,
                "cancelled": cancelled,
            }
        )

        # 落盘
        jsonl_path = self._results_dir / f"{tag}.jsonl"
        with jsonl_path.open("w", encoding="utf-8") as f:
            for row in per_case:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        summary_path = self._results_dir / f"{tag}_summary.json"
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return summary

    def _run_single_case(self, case, run_review_fn, options_cls) -> dict:
        """在隔离的临时工作区中审查单个案例片段。"""
        if not case.snippet_path.is_file():
            raise HarnessError(f"案例片段不存在：{case.snippet_path}")

        with tempfile.TemporaryDirectory(prefix="codesage_eval_") as tmp:
            tmp_path = Path(tmp)
            # 隔离工作区：只放该案例的片段（保证报告的问题都针对目标代码）
            shutil.copy2(case.snippet_path, tmp_path / case.snippet_name)

            report = run_review_fn(
                tmp_path,
                self._settings,
                options_cls(
                    use_llm=not self._options.mock,
                    profile=self._options.profile,
                    quiet=True,
                    # 评测案例跑在临时目录里：不写历史表，避免污染 reviews
                    persist=False,
                ),
            )

        result = evaluate_case(report.findings, case)
        result.update(
            {
                "tokens": report.usage.total,
                "cost_yuan": report.metadata.get("cost_yuan", 0.0),
                "duration_sec": round(report.duration_sec, 2),
                "turn": report.turns,
                "stop_reason": report.stop_reason,
                "reported_titles": [f.title for f in report.sorted_findings()][:5],
            }
        )
        return result


def load_results(results_dir: Path, tag: str) -> tuple[dict, list[dict]]:
    """读取一次评测的 summary 与逐案例结果。"""
    results_dir = Path(results_dir)
    summary_path = results_dir / f"{tag}_summary.json"
    jsonl_path = results_dir / f"{tag}.jsonl"
    if not summary_path.is_file():
        raise HarnessError(f"找不到评测结果：{summary_path}")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    per_case: list[dict] = []
    if jsonl_path.is_file():
        for line in jsonl_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                per_case.append(json.loads(line))
    return summary, per_case

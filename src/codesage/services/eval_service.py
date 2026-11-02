"""评测服务：数据集发现、结果读取、A/B 对比、后台运行。

评测产物仍是 `{results_dir}/{tag}_summary.json` 与 `{tag}.jsonl`（不引入新表），
GUI 只是这些文件的展示层——保证 CLI `eval` 与 GUI 完全一致。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from codesage.core.cancel import CancelToken
from codesage.core.config import Settings
from codesage.harness.compare import compare as compare_summaries
from codesage.harness.dataset import load_dataset
from codesage.harness.runner import EvalOptions, EvalRunner

DEFAULT_RESULTS_DIR = Path(".codesage-eval")


def default_dataset_candidates() -> list[Path]:
    """常见数据集位置（按优先级）。"""
    return [
        Path("examples/eval_dataset"),
        Path("eval_dataset"),
        Path.cwd() / "examples" / "eval_dataset",
    ]


def discover_datasets() -> list[dict[str, Any]]:
    seen: set[str] = set()
    found: list[dict[str, Any]] = []
    for candidate in default_dataset_candidates():
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        key = str(resolved)
        if key in seen or not resolved.is_dir():
            continue
        seen.add(key)
        cases = sorted(resolved.glob("case_*.yaml"))
        if not cases:
            continue
        found.append(
            {
                "path": resolved.as_posix(),
                "name": resolved.name,
                "cases": len(cases),
            }
        )
    return found


def list_results(results_dir: Path = DEFAULT_RESULTS_DIR) -> list[dict[str, Any]]:
    directory = Path(results_dir)
    if not directory.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for path in directory.glob("*_summary.json"):
        try:
            summary = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        tag = str(summary.get("tag") or path.name[: -len("_summary.json")])
        items.append(
            {
                "tag": tag,
                "mock": bool(summary.get("mock")),
                "profile": summary.get("profile", "full"),
                "cases": summary.get("cases", 0),
                "detected": summary.get("detected", 0),
                "recall": summary.get("recall", 0.0),
                "precision": summary.get("precision", 0.0),
                # 文档口径的"误报率"实际是 1 - precision（近似值），这里显式算好
                "false_positive_rate": round(1 - float(summary.get("precision", 0.0)), 4),
                "f1": summary.get("f1", 0.0),
                "total_cost_yuan": summary.get("total_cost_yuan", 0.0),
                "duration_sec": summary.get("duration_sec", 0.0),
                "cancelled": bool(summary.get("cancelled")),
                "created_at": summary.get("created_at")
                or _mtime_text(path, directory),
            }
        )
    items.sort(key=lambda item: item["created_at"] or "", reverse=True)
    return items


def get_result(tag: str, results_dir: Path = DEFAULT_RESULTS_DIR) -> dict[str, Any] | None:
    directory = Path(results_dir)
    summary_path = directory / f"{tag}_summary.json"
    if not summary_path.is_file():
        return None
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    cases: list[dict] = []
    jsonl_path = directory / f"{tag}.jsonl"
    if jsonl_path.is_file():
        for line in jsonl_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    cases.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return {"summary": summary, "cases": cases}


def compare(tag_a: str, tag_b: str, results_dir: Path = DEFAULT_RESULTS_DIR) -> dict:
    return compare_summaries(Path(results_dir), tag_a, tag_b)


def run(
    settings: Settings,
    *,
    dataset: Path,
    tag: str,
    mock: bool,
    sample: int | None,
    profile: str,
    results_dir: Path = DEFAULT_RESULTS_DIR,
    cancel_token: CancelToken | None = None,
    on_phase: Callable[[str, dict], None] | None = None,
) -> dict:
    """同步执行一次评测（由 JobManager 的线程池调用）。"""
    if not mock and not settings.llm_configured:
        raise ValueError("未配置 API Key：请使用 mock 模式（只跑确定性层）或先配置 .env")

    dataset_path = Path(dataset).expanduser()
    if not dataset_path.is_dir():
        raise ValueError(f"数据集目录不存在：{dataset_path}")
    cases = load_dataset(dataset_path)  # 提前校验格式，失败即报错

    total = len(cases[: sample or len(cases)])
    if on_phase:
        on_phase("eval_start", {"tag": tag, "total": total, "dataset": dataset_path.as_posix()})

    def progress(case_id: str, index: int, count: int, result: dict) -> None:
        if on_phase:
            on_phase(
                "eval_case",
                {
                    "case_id": case_id,
                    "index": index,
                    "total": count,
                    "detected": bool(result.get("detected")),
                    "reported": result.get("reported", 0),
                    "recall": round(index / count, 3) if count else 0.0,
                },
            )

    runner = EvalRunner(
        settings,
        dataset_path,
        Path(results_dir),
        EvalOptions(mock=mock, sample=sample, profile=profile),
        on_progress=progress,
        cancel_token=cancel_token,
    )
    return runner.run(tag)


def _mtime_text(path: Path, directory: Path) -> str:
    import time

    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(path.stat().st_mtime))
    except OSError:
        return ""

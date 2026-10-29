"""指标：软匹配 + 检出率 / 误报率 / F1 聚合。

为什么需要"软匹配"（核心工程细节）：
- ground truth 是"第 8-9 行有 SQL 注入"；
- 模型输出是自然语言发现（"execute 使用 f-string 拼接"）；
- 精确文本比对不可行，采用三重软匹配：
    1) 文件名匹配（basename）
    2) 行区间重叠 ≥ 30%
    3) ground truth 关键词至少命中 1 个
  三个条件同时满足才判定"检出"。

匹配器的局限（诚实标注）：
- 同一文件中的其他真实问题会被计入误报（工作区按案例隔离可缓解）；
- 关键词需要人工选择（一般取问题特征词，如 "execute"、"shell"）。
"""

from __future__ import annotations

from codesage.core.models import Finding
from codesage.harness.dataset import EvalCase


def overlap_ratio(a: tuple[int, int], b: tuple[int, int]) -> float:
    """区间重叠比例 = 交集长度 / 较短区间长度。"""
    a_start, a_end = min(a), max(a)
    b_start, b_end = min(b), max(b)
    inter_start = max(a_start, b_start)
    inter_end = min(a_end, b_end)
    if inter_end < inter_start:
        return 0.0
    inter = inter_end - inter_start + 1
    shorter = min(a_end - a_start + 1, b_end - b_start + 1)
    return inter / shorter if shorter > 0 else 0.0


def _file_match(finding_file: str, case: EvalCase) -> bool:
    """文件名匹配：finding 的 basename 与案例片段名一致。"""
    normalized = finding_file.replace("\\", "/").lstrip("./")
    return normalized.endswith(case.snippet_name) or normalized == case.snippet_name


def match_finding(finding: Finding, case: EvalCase, min_overlap: float = 0.3) -> bool:
    """判断一条发现是否命中 ground truth。"""
    if not _file_match(finding.file, case):
        return False

    if overlap_ratio(
        (finding.line_start, finding.line_end),
        (case.line_start, case.line_end),
    ) < min_overlap:
        return False

    if case.keywords:
        haystack = " ".join(
            [finding.title, finding.description, finding.suggestion]
        ).lower()
        if not any(k.lower() in haystack for k in case.keywords):
            return False
    return True


def evaluate_case(findings: list[Finding], case: EvalCase) -> dict:
    """评估单个案例。"""
    matched = [f for f in findings if match_finding(f, case)]
    return {
        "case_id": case.id,
        "category": case.category,
        "detected": len(matched) > 0,
        "matched": len(matched),
        "reported": len(findings),
    }


def aggregate(results: list[dict]) -> dict:
    """聚合指标。

    - recall（检出率）= 检出案例数 / 总案例数
    - precision 近似 = 全部命中发现数 / 全部报告发现数
      （每案例工作区隔离，报告的问题都针对目标片段；
        未命中 ground truth 的报告计入误报侧的近似——见模块 docstring）
    - f1 = 2PR / (P + R)
    """
    total = len(results)
    if total == 0:
        return {
            "cases": 0, "detected": 0, "recall": 0.0,
            "precision": 0.0, "f1": 0.0,
            "reported_total": 0, "matched_total": 0,
        }

    detected = sum(1 for r in results if r["detected"])
    reported_total = sum(int(r["reported"]) for r in results)
    matched_total = sum(int(r["matched"]) for r in results)

    recall = detected / total
    precision = matched_total / reported_total if reported_total else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    return {
        "cases": total,
        "detected": detected,
        "recall": round(recall, 4),
        "precision": round(precision, 4),
        "f1": round(f1, 4),
        "reported_total": reported_total,
        "matched_total": matched_total,
    }

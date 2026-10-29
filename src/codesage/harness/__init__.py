"""Harness 评测框架：把“效果怎么样”变成可量化、可回归、可 A/B 的工程数据。

构成：
- dataset:  评测案例（YAML + 代码片段）
- metrics:  软匹配与指标聚合（检出率/误报率/F1/成本）
- runner:   批量执行器（真实链路驱动，支持 mock 模式跑确定性层）
- compare:  A/B 回归对比
"""

from codesage.harness.compare import compare, load_summary
from codesage.harness.dataset import EvalCase, load_dataset
from codesage.harness.metrics import aggregate, evaluate_case, match_finding
from codesage.harness.runner import EvalOptions, EvalRunner

__all__ = [
    "EvalCase",
    "load_dataset",
    "match_finding",
    "evaluate_case",
    "aggregate",
    "EvalRunner",
    "EvalOptions",
    "compare",
    "load_summary",
]

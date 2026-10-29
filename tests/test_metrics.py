"""指标测试：软匹配器（行区间 + 关键词）与聚合。"""

from pathlib import Path

from codesage.core.models import Category, Finding, Severity, Source
from codesage.harness.dataset import EvalCase
from codesage.harness.metrics import aggregate, evaluate_case, match_finding, overlap_ratio


def make_case() -> EvalCase:
    return EvalCase(
        id="case_001",
        category="security",
        description="SQL 注入",
        snippet_path=Path("/tmp/dataset/snippets/case_001.py"),
        line_start=9,
        line_end=10,
        keywords=["execute"],
        severity="major",
        dataset_dir=Path("/tmp/dataset"),
    )


def make_finding(
    file: str = "case_001.py",
    line_start: int = 9,
    line_end: int = 9,
    title: str = "SQL 注入风险",
    description: str = "cursor.execute 拼接查询",
) -> Finding:
    return Finding(
        file=file,
        line_start=line_start,
        line_end=line_end,
        category=Category.SECURITY,
        severity=Severity.MAJOR,
        title=title,
        description=description,
        source=Source.BANDIT,
    )


def test_overlap_ratio_basic():
    assert overlap_ratio((5, 10), (8, 12)) == 3 / 5  # 交集 3 / 较短区间 5
    assert overlap_ratio((1, 5), (10, 20)) == 0.0
    assert overlap_ratio((3, 3), (3, 3)) == 1.0


def test_match_finding_hit():
    assert match_finding(make_finding(), make_case())


def test_match_finding_line_miss():
    assert not match_finding(make_finding(line_start=50, line_end=50), make_case())


def test_match_finding_file_miss():
    assert not match_finding(make_finding(file="other.py"), make_case())


def test_match_finding_keyword_miss():
    finding = make_finding(title="未使用的导入", description="import 未被使用")
    assert not match_finding(finding, make_case())


def test_evaluate_case():
    case = make_case()
    findings = [make_finding(), make_finding(line_start=50, line_end=50)]
    result = evaluate_case(findings, case)
    assert result["detected"] is True
    assert result["matched"] == 1
    assert result["reported"] == 2


def test_aggregate_metrics():
    results = [
        {"detected": True, "reported": 2, "matched": 1},
        {"detected": False, "reported": 1, "matched": 0},
    ]
    agg = aggregate(results)
    assert agg["cases"] == 2
    assert agg["detected"] == 1
    assert agg["recall"] == 0.5
    assert abs(agg["precision"] - 1 / 3) < 1e-3  # 指标保留 4 位小数
    assert 0 < agg["f1"] < 1


def test_aggregate_empty():
    agg = aggregate([])
    assert agg["cases"] == 0
    assert agg["recall"] == 0.0

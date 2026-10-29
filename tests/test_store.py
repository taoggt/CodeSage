"""存储层测试：全局存储的项目隔离与旧库迁移。

背景：存储从"被审查目录/.codesage"迁移为"用户级 ~/.codesage/store.db"，
记录用 project 列区分项目——本文件锁定隔离语义与向后兼容。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from codesage.core.models import ReviewReport
from codesage.memory.store import Store


def make_store(tmp_path: Path) -> Store:
    return Store(tmp_path / "store.db")


def test_lessons_isolated_by_project(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.add_lesson("false_positive", "项目A的经验", project="/proj/a")
    store.add_lesson("false_positive", "通用经验", project="")

    in_a = store.lessons(project="/proj/a")
    in_b = store.lessons(project="/proj/b")

    assert any("项目A" in r["content"] for r in in_a)
    assert any("通用" in r["content"] for r in in_a)      # 项目可见通用经验
    assert not any("项目A" in r["content"] for r in in_b)  # 项目间互不可见
    assert any("通用" in r["content"] for r in in_b)


def test_lesson_exists_scoped_by_project(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.add_lesson("false_positive", "x", project="/proj/a")
    assert store.lesson_exists("false_positive", "x", project="/proj/a")
    assert not store.lesson_exists("false_positive", "x", project="/proj/b")


def test_reviews_filtered_by_project(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.save_review(ReviewReport(target="/proj/a/file.py"), project="/proj/a")
    store.save_review(ReviewReport(target="/proj/b/file.py"), project="/proj/b")

    assert len(store.recent_reviews(project="/proj/a")) == 1
    assert len(store.recent_reviews()) == 2  # 不传 project 展示全部


def test_feedback_stats_filtered(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.save_feedback("id1", "wrong", project="/proj/a")
    store.save_feedback("id2", "missed", project="/proj/b")

    assert store.feedback_stats(project="/proj/a") == {"wrong": 1}
    assert sum(store.feedback_stats().values()) == 2


def test_legacy_db_migration(tmp_path: Path) -> None:
    """旧库（无 project 列）打开时自动补列，历史数据可读、新数据可写。"""
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target TEXT NOT NULL,
            score INTEGER,
            findings_count INTEGER,
            duration_sec REAL,
            tokens INTEGER,
            cost_yuan REAL,
            payload TEXT,
            created_at TEXT
        );
        """
    )
    conn.execute("INSERT INTO reviews (target, score) VALUES ('/old/path.py', 80)")
    conn.commit()
    conn.close()

    store = Store(db)  # 打开触发迁移
    rows = store.recent_reviews()
    assert len(rows) == 1
    assert rows[0]["target"] == "/old/path.py"
    assert rows[0]["project"] == ""  # 新列默认值

    # 迁移后可正常按 project 写入与查询
    store.save_review(ReviewReport(target="/new/file.py"), project="/new")
    assert len(store.recent_reviews(project="/new")) == 1


def test_get_review_payload_roundtrip(tmp_path: Path) -> None:
    """历史详情依赖：payload 存取往返（含发现列表）。"""
    from codesage.core.models import Category, Finding, ReviewReport, Severity, Source

    store = make_store(tmp_path)
    report = ReviewReport(target="/proj/a")
    report.findings = [
        Finding(
            file="a.py",
            line_start=3,
            line_end=3,
            category=Category.SECURITY,
            severity=Severity.MAJOR,
            title="注入风险",
            source=Source.AST_RULE,
            rule_id="CG013",
        )
    ]
    rid = store.save_review(report, project="/proj/a")

    payload = store.get_review_payload(rid)
    assert payload is not None
    assert payload["target"] == "/proj/a"

    restored = ReviewReport.from_dict(payload)
    assert len(restored.findings) == 1
    assert restored.findings[0].rule_id == "CG013"

    assert store.get_review_payload(99999) is None  # 不存在的 ID

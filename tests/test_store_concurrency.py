"""存储并发与分页：GUI 引入后新增的行为，CLI 语义必须保持不变。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from codesage.core.models import ReviewReport
from codesage.memory.store import Store


def test_wal_and_pragmas(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.db")
    try:
        assert store._conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert store._conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    finally:
        store.close()


def test_concurrent_writes_from_multiple_threads(tmp_path: Path) -> None:
    """每个线程各自一个 Store（与 GUI 每请求一连接一致），并发写不丢不炸。"""
    db = tmp_path / "s.db"
    Store(db).close()  # 先建库，避免并发首启同时做 DDL

    def worker(index: int) -> None:
        store = Store(db)
        try:
            for _ in range(5):
                store.save_review(ReviewReport(target=f"/p/{index}.py"), project="/p")
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(worker, range(8)))

    store = Store(db)
    try:
        assert store.review_count() == 40
    finally:
        store.close()


def test_pagination_and_total(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.db")
    try:
        for index in range(5):
            store.save_review(ReviewReport(target=f"/p/{index}.py"), project="/p")

        first, total = store.list_reviews(limit=2, offset=0, project="/p")
        second, _ = store.list_reviews(limit=2, offset=2, project="/p")
        assert total == 5
        assert len(first) == 2 and len(second) == 2
        assert first[0]["id"] > first[1]["id"]  # 倒序
        assert {row["id"] for row in first}.isdisjoint({row["id"] for row in second})
    finally:
        store.close()


def test_by_severity_persisted_for_listing(tmp_path: Path) -> None:
    from codesage.core.models import Category, Finding, ReviewReport, Severity, Source

    store = Store(tmp_path / "s.db")
    try:
        report = ReviewReport(target="/p/a.py")
        report.findings = [
            Finding(
                file="a.py",
                line_start=1,
                line_end=1,
                category=Category.SECURITY,
                severity=Severity.CRITICAL,
                title="注入",
                source=Source.AST_RULE,
            )
        ]
        store.save_review(report, project="/p")
        rows, _ = store.list_reviews(project="/p")
        assert rows[0]["by_severity"]["critical"] == 1
        assert rows[0]["partial"] is False
    finally:
        store.close()


def test_purge_legacy_eval_rows(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.db")
    try:
        store.save_review(
            ReviewReport(target="C:/Temp/codesage_eval_abc/a.py"), project="/p"
        )
        store.save_review(ReviewReport(target="/p/real.py"), project="/p")
        assert store.purge_legacy_eval_rows() == 1
        assert store.review_count() == 1
    finally:
        store.close()


def test_lesson_delete_and_lookup(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.db")
    try:
        lesson_id = store.add_lesson("false_positive", "案例", project="/p")
        assert store.lesson_by_id(lesson_id)["content"] == "案例"
        assert store.delete_lesson(lesson_id) == 1
        assert store.lesson_by_id(lesson_id) is None
    finally:
        store.close()

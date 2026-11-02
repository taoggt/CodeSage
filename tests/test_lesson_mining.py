"""提炼的幂等性：反馈 → 经验 是单向的，删除经验不应被"再提炼"复活。

背景（真实 bug）：`mine_from_feedback` 之前每次遍历全部反馈，只靠
`lesson_exists` 去重。用户删掉一条经验后，那条反馈仍在，再点一次提炼
就把它"复活"了。修复方案：给 feedback 加 `mined` 标记，提炼后打标，
删除经验不回退标记；要恢复需显式调用 reset。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from codesage.memory.store import Store
from codesage.optim.lessons import LessonBook


def _seed(store: Store, project: str) -> None:
    store.save_feedback("f1", "wrong", file="a.py", title="误报A", project=project)
    store.save_feedback("f2", "missed", file="b.py", title="漏报B", project=project)
    store.save_feedback("f3", "right", file="c.py", title="确认C", project=project)


def test_mine_then_delete_does_not_resurrect(tmp_path: Path) -> None:
    project = tmp_path / "p"
    project.mkdir()
    store = Store(tmp_path / "s.db")
    try:
        _seed(store, str(project))
        assert store.unmined_feedback_count(project=str(project)) == 2  # right 不计入

        book = LessonBook(store)
        assert book.mine_from_feedback() == {"false_positive": 1, "false_negative": 1}
        assert store.unmined_feedback_count(project=str(project)) == 0

        # 删掉误报那条经验 → 再次提炼不应复活
        false_positive = next(
            row for row in store.lessons(project=str(project))
            if row["kind"] == "false_positive"
        )
        store.delete_lesson(int(false_positive["id"]))

        assert book.mine_from_feedback() == {"false_positive": 0, "false_negative": 0}
        assert store.lesson_count(project=str(project)) == 1  # 只剩漏报那条
    finally:
        store.close()


def test_reset_allows_remining(tmp_path: Path) -> None:
    project = tmp_path / "p"
    project.mkdir()
    store = Store(tmp_path / "s.db")
    try:
        _seed(store, str(project))
        book = LessonBook(store)
        book.mine_from_feedback()

        false_positive = next(
            row for row in store.lessons(project=str(project))
            if row["kind"] == "false_positive"
        )
        store.delete_lesson(int(false_positive["id"]))

        # 显式重置标记后，历史反馈可重新提炼
        assert store.reset_feedback_mined(project=str(project)) == 2
        assert book.mine_from_feedback()["false_positive"] == 1
    finally:
        store.close()


def test_right_verdict_is_never_counted_as_pending(tmp_path: Path) -> None:
    project = tmp_path / "p"
    project.mkdir()
    store = Store(tmp_path / "s.db")
    try:
        store.save_feedback("f3", "right", file="c.py", title="确认", project=str(project))
        assert store.unmined_feedback_count(project=str(project)) == 0
        assert LessonBook(store).mine_from_feedback() == {
            "false_positive": 0,
            "false_negative": 0,
        }
        # right 不产生经验，也不该被标记 mined（它本来就不在提炼范围）
        assert store.all_feedback(only_unmined=True)[0]["mined"] == 0
    finally:
        store.close()


def test_v2_migration_marks_existing_feedback_as_mined(tmp_path: Path) -> None:
    """升级前的老反馈没有 mined 列，迁移时应视为"已提炼"，避免复活已删经验。"""
    db = tmp_path / "v2.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            finding_id TEXT NOT NULL,
            verdict TEXT NOT NULL,
            project TEXT DEFAULT '',
            category TEXT DEFAULT '',
            file TEXT DEFAULT '',
            title TEXT DEFAULT '',
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        );
        """
    )
    conn.execute(
        "INSERT INTO feedback (finding_id, verdict, file, title) VALUES ('f1', 'wrong', 'a.py', 'x')"
    )
    conn.execute("PRAGMA user_version=2")
    conn.commit()
    conn.close()

    store = Store(db)
    try:
        rows = store.all_feedback()
        assert rows[0]["mined"] == 1  # 迁移后默认已提炼
        assert store.unmined_feedback_count() == 0
    finally:
        store.close()

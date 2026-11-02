"""项目路径归一化：正/反斜杠与尾部分隔符不应影响过滤结果。

背景（真实 bug）：GUI 的 workspace root 走 `Path.as_posix()`（正斜杠），
而 pipeline 落库用 `str(workspace)`（Windows 反斜杠）。SQL 是字符串全等比较，
导致"报告页显示已标记误报，错题本却是 0 条"。

为什么不用 `COLLATE NOCASE` 兜底大小写：
SQLite 遇到 `project = ? COLLATE NOCASE OR project = ''` 时，OR 优化会改用
BINARY 索引做等值查找，NOCASE 被静默绕过（同一个参数单独查命中、加 OR 就变 0）。
统一走 `Path.resolve()` 归一 + 精确比较，行为可预测。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from codesage.core.models import ReviewReport
from codesage.memory.store import Store


def test_feedback_query_accepts_posix_form(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    store = Store(tmp_path / "s.db")
    try:
        store.save_feedback("f1", "wrong", project=str(project))

        posix = str(project).replace("\\", "/")
        assert store.feedback_stats(project=posix) == {"wrong": 1}
        assert store.feedback_count(project=posix) == 1
        assert len(store.all_feedback(project=posix)) == 1
    finally:
        store.close()


def test_lessons_query_accepts_posix_form(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    store = Store(tmp_path / "s.db")
    try:
        store.add_lesson("false_positive", "案例", project=str(project))

        posix = str(project).replace("\\", "/")
        assert store.lesson_count(project=posix) == 1
        assert any(row["content"] == "案例" for row in store.lessons(project=posix))
    finally:
        store.close()


def test_query_ignores_trailing_separator(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    store = Store(tmp_path / "s.db")
    try:
        store.add_lesson("false_negative", "漏报案例", project=str(project))
        assert store.lesson_count(project=str(project) + os.sep) == 1
        assert store.lesson_count(project=str(project) + "/") == 1
    finally:
        store.close()


@pytest.mark.skipif(os.name != "nt", reason="大小写归一依赖 Windows 的 realpath 行为")
def test_existing_project_case_is_restored_by_resolve(tmp_path: Path) -> None:
    """Windows 下目录存在时，resolve() 会把盘符/目录名纠正成磁盘上的真实大小写。"""
    project = tmp_path / "MixedCase"
    project.mkdir()
    store = Store(tmp_path / "s.db")
    try:
        store.add_lesson("false_positive", "案例", project=str(project))
        assert store.lesson_count(project=str(project).upper()) == 1
    finally:
        store.close()


def test_write_normalizes_project_column(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    store = Store(tmp_path / "s.db")
    try:
        store.save_review(
            ReviewReport(target=str(project / "a.py")),
            project=str(project).replace("\\", "/"),  # 模拟 GUI 传来的正斜杠
        )
        rows, _ = store.list_reviews(project=str(project))
        assert len(rows) == 1
        # 落库的是解析后的规范写法，后续任何形式的查询都能对上
        assert rows[0]["project"] == str(project.resolve())
    finally:
        store.close()


def test_generic_lessons_visible_for_any_project(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    store = Store(tmp_path / "s.db")
    try:
        store.add_lesson("false_positive", "通用经验", project="")
        assert store.lesson_count(project=str(project)) == 1
    finally:
        store.close()


def test_lesson_dedupe_across_path_forms(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    store = Store(tmp_path / "s.db")
    try:
        store.add_lesson("false_positive", "同一条", project=str(project))
        posix = str(project).replace("\\", "/")
        assert store.lesson_exists("false_positive", "同一条", project=posix)
    finally:
        store.close()

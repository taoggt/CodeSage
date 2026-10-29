"""--changed 增量审查模式的集成测试（需要 git）。

锁定行为：
- 仓库无变更 → 明确报错（不静默全量）；
- 有变更 → 只审查变更文件；
- 非 git 仓库 → 回退全量（附提示事件）。
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from codesage.core.config import ReviewSettings, Settings
from codesage.pipeline import ReviewOptions, run_review

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="需要 git")


def make_settings(tmp_path: Path) -> Settings:
    """测试配置：存储指向临时目录，避免污染用户级 ~/.codesage。"""
    return Settings(
        review=ReviewSettings(store_path=str(tmp_path / ".test_store.db"))
    )


def init_repo(root: Path) -> None:
    """初始化 git 仓库并把当前文件提交为基线。"""

    def run(*args: str) -> None:
        subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
        )

    run("init", "-q")
    run("config", "user.email", "test@example.com")
    run("config", "user.name", "test")
    run("add", ".")
    run("commit", "-qm", "init")


def test_changed_no_changes_raises(tmp_path: Path) -> None:
    """仓库无任何变更：应明确报错，而不是静默全量。"""
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    init_repo(tmp_path)

    with pytest.raises(ValueError, match="未检测到 git 变更"):
        run_review(
            tmp_path, make_settings(tmp_path), ReviewOptions(use_llm=False, changed_only=True)
        )


def test_changed_only_reviews_modified_files(tmp_path: Path) -> None:
    """只有 app.py 被修改：审查范围应收缩到 1 个文件。"""
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "other.py").write_text("y = 1\n", encoding="utf-8")
    init_repo(tmp_path)

    (tmp_path / "app.py").write_text("x = 2\n", encoding="utf-8")  # 仅修改 app.py

    report = run_review(
        tmp_path, make_settings(tmp_path), ReviewOptions(use_llm=False, changed_only=True)
    )
    assert report.files_scanned == 1


def test_full_review_scans_everything(tmp_path: Path) -> None:
    """对照组：去掉 --changed 时全量审查。"""
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "other.py").write_text("y = 1\n", encoding="utf-8")
    init_repo(tmp_path)
    (tmp_path / "app.py").write_text("x = 2\n", encoding="utf-8")

    report = run_review(tmp_path, make_settings(tmp_path), ReviewOptions(use_llm=False))
    assert report.files_scanned == 2


def test_non_repo_falls_back_to_full(tmp_path: Path) -> None:
    """非 git 仓库：回退全量审查（附事件提示）。"""
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    events: list[str] = []

    report = run_review(
        tmp_path,
        make_settings(tmp_path),
        ReviewOptions(
            use_llm=False,
            changed_only=True,
            on_event=lambda name, payload: events.append(name),
        ),
    )
    assert report.files_scanned == 1
    assert "changed_fallback" in events

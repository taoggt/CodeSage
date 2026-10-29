r"""CLI 辅助逻辑测试：导出路径解析（-o 目录感知）。

背景：用户用 `-o D:\WCMsg`（已存在的目录）期望"导出到该目录"，
旧代码把目录当文件写入报 PermissionError——本文件锁定修复后的行为。
"""

from pathlib import Path

from codesage.cli.commands.history import _normalize_project
from codesage.cli.commands.review import _resolve_output_path
from codesage.core.models import ReviewReport


def test_directory_output_gets_generated_filename(tmp_path: Path) -> None:
    """-o 给目录：按 <目标名>.codesage.md 落名。"""
    report = ReviewReport(target="C:/projects/demo/test.py")
    resolved = _resolve_output_path(tmp_path, report, "markdown")
    assert resolved == tmp_path / "test.codesage.md"


def test_directory_output_json_extension(tmp_path: Path) -> None:
    report = ReviewReport(target="C:/projects/demo/test.py")
    resolved = _resolve_output_path(tmp_path, report, "json")
    assert resolved == tmp_path / "test.codesage.json"


def test_directory_target_name_used(tmp_path: Path) -> None:
    """审查目标是目录时，取目录名作为文件名。"""
    report = ReviewReport(target="C:/projects/demo")
    resolved = _resolve_output_path(tmp_path, report, "markdown")
    assert resolved == tmp_path / "demo.codesage.md"


def test_file_output_path_unchanged(tmp_path: Path) -> None:
    """-o 给不存在的文件路径：原样使用（正常导出流程）。"""
    report = ReviewReport(target="C:/projects/demo/test.py")
    out = tmp_path / "report.md"
    assert _resolve_output_path(out, report, "markdown") == out


def test_existing_file_not_treated_as_directory(tmp_path: Path) -> None:
    """已存在的文件不是目录：原样使用（覆盖导出）。"""
    report = ReviewReport(target="C:/projects/demo/test.py")
    out = tmp_path / "report.md"
    out.write_text("old content", encoding="utf-8")
    assert _resolve_output_path(out, report, "markdown") == out


# ---------------------------------------------------------------- --project 归一化


def test_project_normalization_file_to_parent(tmp_path: Path) -> None:
    """--project 传文件路径 → 归一到父目录（对齐写入端语义）。

    用户反馈：库中 project 存的是文件所在目录（单文件审查时），
    查询端传文件路径精确匹配失败。
    """
    target = tmp_path / "test.py"
    target.write_text("x = 1\n", encoding="utf-8")
    assert _normalize_project(target) == str(tmp_path.resolve())


def test_project_normalization_directory_kept(tmp_path: Path) -> None:
    """--project 传目录 → 原样。"""
    assert _normalize_project(tmp_path) == str(tmp_path.resolve())


def test_project_normalization_missing_file_by_suffix(tmp_path: Path) -> None:
    """文件已删除但带 .py 后缀 → 仍按文件归一到父目录。"""
    missing = tmp_path / "gone.py"
    assert _normalize_project(missing) == str(tmp_path.resolve())


def test_project_normalization_missing_dir_kept(tmp_path: Path) -> None:
    """不存在的无后缀路径（目录已改名等）→ 原样。"""
    missing_dir = tmp_path / "old_dir"
    assert _normalize_project(missing_dir) == str(missing_dir.resolve())

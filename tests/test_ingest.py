"""感知层测试：扫描过滤 / 切块 / git diff 解析。"""

from codesage.core.languages import Language
from codesage.ingest.chunker import chunk_source
from codesage.ingest.filters import FileFilter, SkipReason
from codesage.ingest.git_diff import HunkRange, expand_ranges
from codesage.ingest.scanner import scan


def test_scan_excludes_and_extensions(tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "readme.md").write_text("# readme", encoding="utf-8")
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "cached.py").write_text("x = 1\n", encoding="utf-8")

    result = scan(tmp_path, FileFilter(tmp_path))
    names = {p.name for p in result.files}
    assert names == {"app.py"}


def test_scan_skips_large_files(tmp_path):
    (tmp_path / "big.py").write_text("x = 1\n" * 3000, encoding="utf-8")
    result = scan(tmp_path, FileFilter(tmp_path, max_file_lines=100))
    assert not result.files
    assert any("大文件预过滤" in reason for _, reason in result.skipped)


def test_scan_respects_gitignore(tmp_path):
    (tmp_path / ".gitignore").write_text("generated/\n", encoding="utf-8")
    gen = tmp_path / "generated"
    gen.mkdir()
    (gen / "gen.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("y = 2\n", encoding="utf-8")

    result = scan(tmp_path, FileFilter(tmp_path))
    names = {p.name for p in result.files}
    assert names == {"main.py"}


def test_chunk_small_file_single_chunk():
    chunks = chunk_source("t.py", "x = 1\ny = 2\n", max_chunk_lines=400)
    assert len(chunks) == 1
    assert chunks[0].symbol == "<module>"


def test_chunk_splits_by_function_boundary():
    parts = []
    for i in range(30):
        parts.append(f"def f{i}():\n    return {i}\n")
    source = "\n".join(parts)  # 约 90 行
    chunks = chunk_source("t.py", source, max_chunk_lines=40)
    assert len(chunks) > 1
    assert any(c.kind == "function" for c in chunks)


def test_chunk_syntax_error_returns_whole_file():
    chunks = chunk_source("t.py", "def broken(:\n    pass\n", max_chunk_lines=1)
    assert len(chunks) == 1


def test_expand_ranges_with_padding():
    ranges = [HunkRange(start=10, end=12)]
    assert expand_ranges(ranges, padding=2) == [(8, 14)]
    assert expand_ranges(ranges, padding=0) == [(10, 12)]


# ---------------------------------------------------------------- 多语言准入


def _mixed_repo(root):
    (root / "app.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    java_dir = root / "src" / "main" / "java"
    java_dir.mkdir(parents=True)
    (java_dir / "App.java").write_text("class App {}\n", encoding="utf-8")
    return root


def test_unsupported_language_is_silent_but_counted(tmp_path):
    """非目标语言不逐条进 skipped（否则混合仓库把它们塞爆），但必须计数。

    这个断言锁的是历史坑：旧实现用中文字符串全等判断是否记录，
    改文案就会静默失效，`files_skipped` 统计随之失真。
    """
    _mixed_repo(tmp_path)
    result = scan(tmp_path, FileFilter(tmp_path))  # 默认只审 Python

    assert {p.name for p in result.files} == {"app.py"}
    assert result.skipped == []  # 静默：不逐条记录
    assert result.skip_counts[SkipReason.UNSUPPORTED_LANGUAGE.value] == 1
    assert result.language_counts == {"python": 1}


def test_java_only_scope(tmp_path):
    """显式只审 Java 时，.py 也应走"非目标语言"分支。"""
    _mixed_repo(tmp_path)
    result = scan(tmp_path, FileFilter(tmp_path, languages=(Language.JAVA,)))

    assert {p.name for p in result.files} == {"App.java"}
    assert result.language_counts == {"java": 1}


def test_auto_scope_includes_both_languages(tmp_path):
    """auto 模式（传入全部语言）时混合仓库两个都收。"""
    _mixed_repo(tmp_path)
    result = scan(
        tmp_path, FileFilter(tmp_path, languages=(Language.PYTHON, Language.JAVA))
    )

    assert {p.name for p in result.files} == {"app.py", "App.java"}
    assert result.language_counts == {"python": 1, "java": 1}


def test_java_build_dirs_pruned(tmp_path):
    """target/ 是 Maven 的输出目录，里面是 .class 与生成源码，必须剪掉。"""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "A.java").write_text("class A {}\n", encoding="utf-8")
    target = tmp_path / "target" / "generated"
    target.mkdir(parents=True)
    (target / "B.java").write_text("class B {}\n", encoding="utf-8")

    result = scan(
        tmp_path,
        FileFilter(tmp_path, languages=(Language.JAVA,), extra_excludes=("target",)),
    )
    assert {p.name for p in result.files} == {"A.java"}


def test_language_specific_line_limit(tmp_path):
    """Java 的行数阈值应覆盖全局默认值（巨型 Service 类不能被静默跳过）。"""
    _mixed_repo(tmp_path)
    file_filter = FileFilter(
        tmp_path, max_file_lines=10, languages=(Language.PYTHON, Language.JAVA)
    )
    assert file_filter.max_lines_for(tmp_path / "App.java") == 3000
    assert file_filter.max_lines_for(tmp_path / "app.py") == 10


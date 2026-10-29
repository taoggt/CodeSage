"""感知层测试：扫描过滤 / 切块 / git diff 解析。"""

from codesage.ingest.chunker import chunk_source
from codesage.ingest.filters import FileFilter
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

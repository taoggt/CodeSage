"""工作区服务：目录登记、文件树、源码读取、扫描预检、"是否已审过"。

安全边界（GUI 会暴露 HTTP 读取能力，必须守住）：
1. 只有显式登记过的根目录才可访问（未登记 → 403）；
2. 所有相对路径 resolve 后必须仍在该根目录内（防 ../ 穿越、防符号链接逃逸）；
3. 敏感文件（.env / 私钥 / 数据库）永久拒绝，即使在工作区内；
4. 只读文本、单次窗口上限 2000 行、单文件上限 1.5MB。
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from codesage.core.config import Settings
from codesage.ingest.filters import FileFilter
from codesage.ingest.git_diff import changed_line_ranges, is_git_repo
from codesage.ingest.scanner import count_lines, scan
from codesage.memory.store import Store

MAX_WINDOW_LINES = 2000
MAX_FILE_BYTES = 1_500_000
MAX_TREE_ENTRIES = 2000
MAX_LISTED_FILES = 500

ALLOWED_SUFFIXES = {
    ".py", ".pyi", ".md", ".txt", ".toml", ".yaml", ".yml", ".json",
    ".cfg", ".ini", ".js", ".jsx", ".ts", ".tsx", ".css", ".html",
    ".sql", ".sh", ".bat", ".ps1", ".java", ".go", ".rs",
}

# 文件名命中即拒绝（不看后缀）：密钥、环境变量、数据库
SENSITIVE_NAMES = {
    ".env", ".env.local", ".env.production", ".env.development",
    "id_rsa", "id_ed25519", "credentials", ".netrc", ".npmrc", ".pypirc",
}
SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".db", ".sqlite", ".sqlite3", ".keystore"}
SENSITIVE_DIRS = {".git", ".codesage", "node_modules", "__pycache__"}


class WorkspaceError(ValueError):
    """工作区相关的业务错误（由路由映射为 4xx）。"""

    def __init__(self, message: str, code: str = "workspace_error") -> None:
        super().__init__(message)
        self.code = code


class PathOutsideRoot(WorkspaceError):
    def __init__(self, path: str) -> None:
        super().__init__(f"路径不在工作区内：{path}", code="path_outside_root")


class PathForbidden(WorkspaceError):
    def __init__(self, path: str) -> None:
        super().__init__(f"该文件不允许读取：{path}", code="path_forbidden")


class PathUnsupported(WorkspaceError):
    def __init__(self, path: str) -> None:
        super().__init__(f"不支持预览该类型文件：{path}", code="unsupported_media")


class WorkspaceRegistry:
    """已登记的工作区根目录（进程内，重启后由前端重新登记）。"""

    def __init__(self, limit: int = 20) -> None:
        self._roots: dict[str, Path] = {}
        self._lock = threading.RLock()
        self._limit = limit

    def register(self, path: str | Path) -> Path:
        resolved = Path(path).expanduser().resolve()
        if not resolved.exists() or not resolved.is_dir():
            raise WorkspaceError(f"目录不存在：{resolved}", code="invalid_path")
        key = str(resolved)
        with self._lock:
            self._roots.pop(key, None)  # 重新登记置顶（简易 LRU）
            self._roots[key] = resolved
            while len(self._roots) > self._limit:
                oldest = next(iter(self._roots))
                self._roots.pop(oldest, None)
        return resolved

    def get(self, path: str | Path) -> Path:
        resolved = Path(path).expanduser().resolve()
        with self._lock:
            if str(resolved) in self._roots:
                return resolved
            # 允许登记目录的子目录（用户点进子目录时不要求重新登记）
            for root in self._roots.values():
                if _is_within(resolved, root):
                    return resolved
        raise PathOutsideRoot(str(path))

    def list(self) -> list[str]:
        with self._lock:
            return [str(p) for p in self._roots.values()]

    def unregister(self, path: str | Path) -> bool:
        """移除一条工作区登记（只影响"最近打开"，不动磁盘上的目录）。"""
        try:
            resolved = Path(path).expanduser().resolve()
        except OSError:
            return False
        with self._lock:
            return self._roots.pop(str(resolved), None) is not None


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _normcase(path: Path) -> str:
    return os.path.normcase(str(path))


def resolve_inside(root: Path, relative: str) -> Path:
    """把相对路径解析为根目录内的绝对路径，越界即拒绝。"""
    rel = (relative or "").replace("\\", "/").lstrip("/")
    candidate = (root / rel).resolve()
    if not _is_within(candidate, root):
        raise PathOutsideRoot(relative)
    return candidate


def _ensure_readable(path: Path) -> None:
    name = path.name.lower()
    if name in SENSITIVE_NAMES or name.startswith(".env"):
        raise PathForbidden(path.name)
    if path.suffix.lower() in SENSITIVE_SUFFIXES:
        raise PathForbidden(path.name)
    if any(part.lower() in SENSITIVE_DIRS for part in path.parts):
        raise PathForbidden(path.name)
    if path.suffix.lower() not in ALLOWED_SUFFIXES:
        raise PathUnsupported(path.name)


# ------------------------------------------------------------------ 打开 / 概览


def open_workspace(path: str | Path, settings: Settings, registry: WorkspaceRegistry) -> dict:
    """登记并返回工作区概览（文件数、跳过数、git 状态、可用分析器）。"""
    root = registry.register(path)
    file_filter = FileFilter(
        root,
        excludes=list(settings.review.ignore_dirs),
        max_file_lines=settings.review.max_file_lines,
    )
    result = scan(root, file_filter)
    return {
        "root": root.as_posix(),
        "root_raw": str(root),
        "name": root.name or root.drive,
        "files": len(result.files),
        "skipped": len(result.skipped),
        "git": git_info(root),
        "analyzer_ready": analyzer_readiness(),
    }


def analyzer_readiness() -> dict[str, bool]:
    from codesage.analyzers import default_analyzers

    readiness = {"ast_rule": True}
    for analyzer in default_analyzers():
        try:
            readiness[analyzer.name] = bool(analyzer.is_available())
        except Exception:  # noqa: BLE001
            readiness[analyzer.name] = False
    return readiness


def git_info(root: Path) -> dict:
    if not is_git_repo(root):
        return {"is_repo": False, "branch": "", "changed_files": [], "changed_count": 0}
    branch = ""
    try:
        import subprocess

        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode == 0:
            branch = proc.stdout.strip()
    except (OSError, Exception):  # noqa: BLE001
        branch = ""
    ranges = changed_line_ranges(root)
    changed = sorted(ranges.keys())
    return {
        "is_repo": True,
        "branch": branch,
        "changed_files": changed[:200],
        "changed_count": len(changed),
    }


def scan_preview(
    root: Path,
    target: str | None,
    settings: Settings,
) -> dict:
    """零 Token 预检：列出会被审查 / 会被跳过的文件（回答"这次到底审什么"）。"""
    resolved_target = resolve_inside(root, target) if target else root
    if not resolved_target.exists():
        raise WorkspaceError(f"路径不存在：{target}", code="invalid_path")
    workspace = resolved_target if resolved_target.is_dir() else resolved_target.parent
    file_filter = FileFilter(
        workspace,
        excludes=list(settings.review.ignore_dirs),
        max_file_lines=settings.review.max_file_lines,
    )
    result = scan(resolved_target, file_filter)
    included = [
        _rel_posix(p, workspace) for p in result.files[:MAX_LISTED_FILES]
    ]
    skipped = [
        {"file": _rel_posix(Path(f), workspace), "reason": r}
        for f, r in result.skipped[:200]
    ]
    return {
        "target": resolved_target.as_posix(),
        "workspace": workspace.as_posix(),
        "included": included,
        "included_count": len(result.files),
        "skipped": skipped,
        "skipped_count": len(result.skipped),
    }


# ------------------------------------------------------------------ 目录浏览


_SKIP_BROWSE_DIRS = {
    "$RECYCLE.BIN",
    "System Volume Information",
    "Config.Msi",
    "Recovery",
}


def browse(path: str | None = None, limit: int = 500) -> dict:
    """目录浏览器：path 为空时返回"根视图"（Windows 盘符 / 其他系统的 /）。

    只列目录、不读文件——用途是让用户点选一个工作区，而不是当文件管理器用。
    """
    shortcuts = _system_shortcuts()

    if not path:
        return {
            "path": "",
            "parent": None,
            "entries": _root_entries(),
            "shortcuts": shortcuts,
            "at_root": True,
        }

    base = Path(path).expanduser()
    try:
        base = base.resolve()
    except OSError:
        pass
    if not base.is_dir():
        raise WorkspaceError(f"目录不存在：{path}", code="invalid_path")

    try:
        children = sorted(base.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError as exc:
        raise WorkspaceError(f"没有权限读取：{base}", code="invalid_path") from exc
    except OSError as exc:
        raise WorkspaceError(f"无法读取目录：{exc}", code="invalid_path") from exc

    entries: list[dict] = []
    for child in children:
        if len(entries) >= limit:
            break
        try:
            if not child.is_dir():
                continue
        except OSError:
            continue
        if child.name in _SKIP_BROWSE_DIRS or child.name.startswith("."):
            continue
        entries.append({"name": child.name, "path": str(child), "is_dir": True})

    parent = base.parent
    return {
        "path": str(base),
        "parent": str(parent) if parent != base else None,
        "entries": entries,
        "shortcuts": shortcuts,
        "at_root": False,
    }


def _root_entries() -> list[dict]:
    """Windows 列盘符；其他系统给根目录。"""
    if os.name != "nt":
        return [{"name": "/", "path": "/", "is_dir": True}]
    try:
        import ctypes

        mask = ctypes.windll.kernel32.GetLogicalDrives()  # type: ignore[attr-defined]
        return [
            {"name": f"{chr(65 + index)}:", "path": f"{chr(65 + index)}:\\", "is_dir": True}
            for index in range(26)
            if mask & (1 << index)
        ]
    except Exception:  # noqa: BLE001 - 极端环境下退回探测 C/D
        return [
            {"name": f"{letter}:", "path": f"{letter}:\\", "is_dir": True}
            for letter in ("C", "D")
            if Path(f"{letter}:\\").exists()
        ]


def _system_shortcuts() -> list[dict]:
    home = Path.home()
    candidates = [
        ("主目录", home),
        ("桌面", home / "Desktop"),
        ("文档", home / "Documents"),
        ("下载", home / "Downloads"),
        ("当前目录", Path.cwd()),
    ]
    return [
        {"label": label, "path": str(path)}
        for label, path in candidates
        if path.is_dir()
    ]


# ------------------------------------------------------------------ 文件树 / 读取


def list_tree(root: Path, relative: str = "", depth: int = 1) -> dict:
    """惰性文件树（每次只列一层，前端按需展开）。"""
    base = resolve_inside(root, relative)
    if not base.is_dir():
        raise WorkspaceError(f"不是目录：{relative}", code="invalid_path")
    depth = max(1, min(depth, 3))
    entries: list[dict] = []

    def walk(directory: Path, level: int) -> None:
        if len(entries) >= MAX_TREE_ENTRIES:
            return
        try:
            children = sorted(
                directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())
            )
        except OSError:
            return
        for child in children:
            if len(entries) >= MAX_TREE_ENTRIES:
                return
            name = child.name
            if child.is_dir():
                if name in SENSITIVE_DIRS or name.endswith(".egg-info"):
                    continue
                entries.append(
                    {
                        "path": _rel_posix(child, root),
                        "name": name,
                        "type": "dir",
                        "has_children": _has_children(child),
                    }
                )
                if level < depth:
                    walk(child, level + 1)
            else:
                if child.suffix.lower() not in ALLOWED_SUFFIXES:
                    continue
                if name.lower() in SENSITIVE_NAMES:
                    continue
                entries.append(
                    {
                        "path": _rel_posix(child, root),
                        "name": name,
                        "type": "file",
                        "is_python": child.suffix == ".py",
                        "size": _safe_size(child),
                    }
                )

    walk(base, 1)
    return {"root": root.as_posix(), "base": _rel_posix(base, root), "entries": entries}


def read_file(
    root: Path,
    relative: str,
    start: int | None = None,
    end: int | None = None,
) -> dict:
    """读取源码窗口（默认前 2000 行）。"""
    path = resolve_inside(root, relative)
    if not path.is_file():
        raise WorkspaceError(f"文件不存在：{relative}", code="invalid_path")
    _ensure_readable(path)

    size = _safe_size(path)
    if size > MAX_FILE_BYTES:
        raise WorkspaceError(
            f"文件过大（{size // 1024} KB），请直接在编辑器中打开", code="file_too_large"
        )

    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise WorkspaceError(f"读取失败：{exc}", code="read_failed") from exc
    if b"\x00" in raw[:8192]:
        raise PathUnsupported(path.name)

    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    total = len(lines)
    first = max(1, start or 1)
    last = min(total, end if end else first + MAX_WINDOW_LINES - 1)
    if last < first:
        first, last = 1, min(total, MAX_WINDOW_LINES)
    window = lines[first - 1 : last]

    return {
        "path": _rel_posix(path, root),
        "total_lines": total,
        "start": first,
        "end": last,
        "content": "\n".join(window),
        "truncated": last < total or first > 1,
        "language": _language(path.suffix),
        "size": size,
    }


# ------------------------------------------------------------------ 审查状态


def workspace_status(root: Path, store: Store) -> dict:
    """回答"这个项目审过没有、哪些文件有历史问题、有没有改动后未复审"。"""
    rows = store.recent_reviews(limit=1, project=str(root))
    if not rows:
        return {
            "root": root.as_posix(),
            "latest_review": None,
            "file_coverage": {},
            "stale": True,
            "stale_files": [],
            # 常见情况：用户审的是 src/ 或 tests/，根目录本身没有记录。
            # 这里如实列出子目录的记录（不做路径映射，避免给出错误的覆盖率）。
            "subproject_reviews": _subproject_reviews(root, store),
        }

    row = rows[0]
    payload = store.get_review_payload(int(row["id"])) or {}
    metadata = payload.get("metadata") or {}
    findings = payload.get("findings") or []

    coverage: dict[str, dict] = {}
    for item in findings:
        rel = str(item.get("file") or "")
        if not rel:
            continue
        bucket = coverage.setdefault(
            rel, {"reported": True, "findings": 0, "severity": {}}
        )
        bucket["findings"] += 1
        severity = str(item.get("severity") or "")
        bucket["severity"][severity] = bucket["severity"].get(severity, 0) + 1

    # 审查覆盖过的文件（pipeline 写入 metadata["files"]；旧记录没有该字段）
    scanned_files = metadata.get("files") or []
    for rel in scanned_files:
        coverage.setdefault(rel, {"reported": True, "findings": 0, "severity": {}})

    created_at = str(row.get("created_at") or "")
    review_ts = _parse_local_ts(created_at)
    stale_files: list[str] = []
    if review_ts is not None:
        for rel in scanned_files:
            try:
                mtime = (root / rel).stat().st_mtime
            except OSError:
                continue
            if mtime > review_ts:
                stale_files.append(rel)

    return {
        "root": root.as_posix(),
        "latest_review": {
            "id": int(row["id"]),
            "score": int(row.get("score") or 0),
            "findings_count": int(row.get("findings_count") or 0),
            "by_severity": row.get("by_severity") or {},
            "created_at": created_at,
            "age_human": _humanize_age(created_at),
            "llm_enabled": bool(row.get("llm_enabled")),
            "partial": bool(row.get("partial")),
            "target": str(row.get("target") or ""),
            "files_scanned": int(payload.get("files_scanned") or 0),
        },
        "file_coverage": coverage,
        "stale": bool(stale_files),
        "stale_files": stale_files[:200],
        "subproject_reviews": [],
    }


def _subproject_reviews(root: Path, store: Store, limit: int = 3) -> list[dict]:
    """找出"根目录之下、被单独审查过"的子目录记录（每个项目只取最新一条）。"""
    try:
        prefix = os.path.normcase(str(root.resolve())) + os.sep
    except OSError:
        return []

    items: list[dict] = []
    seen: set[str] = set()
    for row in store.recent_reviews(limit=60):
        project = str(row.get("project") or "")
        if not project:
            continue
        normalized = os.path.normcase(str(Path(project)))
        if not normalized.startswith(prefix) or normalized in seen:
            continue
        seen.add(normalized)
        items.append(
            {
                "id": int(row["id"]),
                "project": project,
                "project_name": Path(project).name,
                "relative": _rel_posix(Path(project), root),
                "score": int(row.get("score") or 0),
                "findings_count": int(row.get("findings_count") or 0),
                "created_at": str(row.get("created_at") or ""),
                "age_human": _humanize_age(str(row.get("created_at") or "")),
            }
        )
        if len(items) >= limit:
            break
    return items


# ------------------------------------------------------------------ 工具函数


def _rel_posix(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def _has_children(path: Path) -> bool:
    try:
        for child in path.iterdir():
            if child.is_dir() and child.name not in SENSITIVE_DIRS:
                return True
            if child.is_file() and child.suffix in ALLOWED_SUFFIXES:
                return True
    except OSError:
        return False
    return False


def _safe_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _language(suffix: str) -> str:
    mapping = {
        ".py": "python", ".pyi": "python", ".ts": "typescript", ".tsx": "tsx",
        ".js": "javascript", ".jsx": "jsx", ".json": "json", ".md": "markdown",
        ".toml": "toml", ".yaml": "yaml", ".yml": "yaml", ".html": "html",
        ".css": "css", ".sql": "sql", ".sh": "bash", ".java": "java",
        ".go": "go", ".rs": "rust",
    }
    return mapping.get(suffix.lower(), "text")


def _parse_local_ts(text: str) -> float | None:
    try:
        return time.mktime(datetime.strptime(text, "%Y-%m-%d %H:%M:%S").timetuple())
    except (ValueError, TypeError, OverflowError):
        return None


def _humanize_age(text: str) -> str:
    ts = _parse_local_ts(text)
    if ts is None:
        return ""
    delta = time.time() - ts
    if delta < 60:
        return "刚刚"
    if delta < 3600:
        return f"{int(delta // 60)} 分钟前"
    if delta < 86400:
        return f"{int(delta // 3600)} 小时前"
    if delta < 86400 * 30:
        return f"{int(delta // 86400)} 天前"
    return text


def file_line_count(path: Path) -> int:
    try:
        return count_lines(path)
    except OSError:
        return 0


def summarize_files(root: Path, settings: Settings) -> dict[str, Any]:
    """轻量统计（供概览卡片：按后缀分布）。"""
    result = scan(root, FileFilter(root, excludes=list(settings.review.ignore_dirs)))
    by_ext: dict[str, int] = {}
    for path in result.files:
        by_ext[path.suffix or "(无后缀)"] = by_ext.get(path.suffix or "(无后缀)", 0) + 1
    return {"files": len(result.files), "by_ext": by_ext}

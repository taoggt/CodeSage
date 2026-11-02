"""SQLite 持久化：审查历史 / 反馈 / 错题本 / Prompt 版本。

存储位置（用户级全局存储）：
    默认 ~/.codesage/store.db（可用配置 review.store_path 覆盖）

设计演进说明（修复两个真实问题）：
- 旧方案把库写在"被审查目录/.codesage/"下：一来会**污染被审查项目**
  （审查第三方代码也在人家目录里建文件夹），二来记录散落在各子目录，
  导致 history 看不到（用户实际反馈的困惑）；
- 新方案全局集中存储，用 project 列区分项目（值为审查根目录的绝对路径）：
  - reviews 记录每次审查的 project，history 默认展示全部、可 --project 过滤；
  - feedback / lessons 按 project 隔离——错题本经验只注入同项目的审查
    （project 为空的经验视为通用，对所有项目生效）。

并发设计（GUI 引入后新增，CLI 行为不变）：
- WAL + busy_timeout：读不阻塞写，写冲突自动等待而不是立刻报错；
- isolation_level=None：事务显式管理，写操作用 BEGIN IMMEDIATE 提前拿写锁；
- check_same_thread=False 只是允许连接跨线程使用，**实例仍不跨线程共享**
  （每个请求 / 每个后台任务各自 open 一个 Store）；
- schema 用 PRAGMA user_version 做一次性初始化，避免并发首启争抢 DDL。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from codesage.core.models import ReviewReport

SCHEMA_VERSION = 3
DISMISSED_PAYLOAD_LIMIT = 50  # 落库时截断被驳回候选，避免 payload 无限膨胀

_INIT_LOCK = threading.Lock()


class StoreBusyError(RuntimeError):
    """写锁竞争超时（并发写压力过大）。"""


def default_store_path() -> Path:
    """用户级默认存储路径。"""
    return Path.home() / ".codesage" / "store.db"


def _canonical_project(project: str | None) -> str | None:
    """把项目路径归一成磁盘上的规范写法（Windows 下是反斜杠 + 实际大小写）。

    为什么必须有这一步：写入端是 `str(workspace)`（反斜杠），而 GUI 的
    workspace root 来自 `Path.as_posix()`（正斜杠）。SQL 是字符串全等比较，
    不归一化就会出现"反馈确实写进去了，错题本却查不到"的假象。
    """
    if not project:
        return project
    try:
        return str(Path(project).expanduser().resolve())
    except (OSError, RuntimeError):  # 盘符不存在等极端情况：原样比较
        return project


_SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target TEXT NOT NULL,
    project TEXT DEFAULT '',
    score INTEGER,
    findings_count INTEGER,
    duration_sec REAL,
    tokens INTEGER,
    cost_yuan REAL,
    by_severity TEXT DEFAULT '{}',
    llm_enabled INTEGER DEFAULT 0,
    partial INTEGER DEFAULT 0,
    payload TEXT,
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    finding_id TEXT NOT NULL,
    verdict TEXT NOT NULL,
    project TEXT DEFAULT '',
    category TEXT DEFAULT '',
    file TEXT DEFAULT '',
    title TEXT DEFAULT '',
    note TEXT DEFAULT '',
    mined INTEGER DEFAULT 0,
    mined_at TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    project TEXT DEFAULT '',
    pattern TEXT DEFAULT '',
    content TEXT NOT NULL,
    hits INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS prompts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile TEXT NOT NULL,
    version TEXT NOT NULL,
    template TEXT NOT NULL,
    note TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);
"""

_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_reviews_project_id ON reviews(project, id DESC);
CREATE INDEX IF NOT EXISTS idx_reviews_created ON reviews(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_feedback_finding ON feedback(finding_id);
CREATE INDEX IF NOT EXISTS idx_feedback_project_id ON feedback(project, id DESC);
CREATE INDEX IF NOT EXISTS idx_lessons_project_kind ON lessons(project, kind, id DESC);
"""

# 旧库补齐的列：(表, 列, 定义)
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("reviews", "project", "TEXT DEFAULT ''"),
    ("reviews", "by_severity", "TEXT DEFAULT '{}'"),
    ("reviews", "llm_enabled", "INTEGER DEFAULT 0"),
    ("reviews", "partial", "INTEGER DEFAULT 0"),
    ("feedback", "project", "TEXT DEFAULT ''"),
    ("feedback", "mined", "INTEGER DEFAULT 0"),
    ("feedback", "mined_at", "TEXT DEFAULT ''"),
    ("lessons", "project", "TEXT DEFAULT ''"),
)

_REVIEW_COLUMNS = (
    "id, target, project, score, findings_count, duration_sec, "
    "tokens, cost_yuan, by_severity, llm_enabled, partial, created_at"
)


class Store:
    """SQLite 存储封装。"""

    def __init__(self, db_path: Path | None = None) -> None:
        self._path = Path(db_path) if db_path else default_store_path()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self._path),
            timeout=10.0,
            isolation_level=None,          # 显式事务（autocommit 模式）
            check_same_thread=False,       # 允许跨线程使用，但实例不共享
        )
        self._conn.row_factory = sqlite3.Row
        self._apply_pragmas()
        self._ensure_schema()

    # ------------------------------------------------------------ 初始化

    def _apply_pragmas(self) -> None:
        """连接级 pragma（每次建连都要设，代价极低）。

        注意：`journal_mode=WAL` 是**数据库级持久属性**，只在建库时设一次
        （见 _ensure_schema）。若每个连接都执行，它会尝试拿排他锁，
        与并发写撞车时可能把连接置于 readonly 状态（真实踩过的坑）。
        """
        for statement in (
            "PRAGMA busy_timeout=5000",
            "PRAGMA synchronous=NORMAL",
            "PRAGMA temp_store=MEMORY",
        ):
            try:
                self._conn.execute(statement)
            except sqlite3.DatabaseError:  # 只读介质等场景：不阻断主流程
                continue

    def _ensure_schema(self) -> None:
        """建表 + 迁移 + 索引，同一库文件只做一次（靠 user_version 判断）。"""
        with _INIT_LOCK:
            version = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
            if version >= SCHEMA_VERSION:
                return
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.DatabaseError:
                pass
            self._conn.executescript(_SCHEMA)
            self._migrate()
            self._migrate_data(version)
            self._conn.executescript(_INDEXES)
            self._conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def _migrate_data(self, previous: int) -> None:
        """数据层面的升级（结构升级由 _migrate 负责）。

        v2 → v3：新增 feedback.mined。老反馈没有这个标记，若默认为 0，
        升级后点一次「批量提炼」会把用户已经删掉的经验全部复活。
        因此把已有反馈视为"已提炼"，只对升级后新产生的反馈生效。
        （fresh 库此时 feedback 为空，UPDATE 命中 0 行，无副作用。）
        """
        if previous < 3:
            self._conn.execute(
                "UPDATE feedback SET mined = 1, mined_at = datetime('now', 'localtime') "
                "WHERE verdict IN ('wrong', 'missed') AND mined = 0"
            )

    def _migrate(self) -> None:
        """向后兼容：为旧库补齐缺失列。"""
        for table, column, ddl in _ADDED_COLUMNS:
            cols = {
                str(row[1])
                for row in self._conn.execute(f"PRAGMA table_info({table})")
            }
            if cols and column not in cols:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    @contextmanager
    def _write(self) -> Iterator[None]:
        """写事务：BEGIN IMMEDIATE + 锁冲突退避重试。"""
        delays = (0.05, 0.15, 0.40)
        for attempt in range(len(delays) + 1):
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                try:
                    yield
                except Exception:
                    self._conn.execute("ROLLBACK")
                    raise
                self._conn.execute("COMMIT")
                return
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                if "locked" not in message and "busy" not in message:
                    raise
                if attempt >= len(delays):
                    raise StoreBusyError(f"存储繁忙，请重试：{exc}") from exc
                time.sleep(delays[attempt])

    @property
    def path(self) -> Path:
        return self._path

    # ------------------------------------------------------------ 审查历史

    def save_review(self, report: ReviewReport, project: str = "") -> int:
        project = _canonical_project(project) or ""
        payload = report.to_dict()
        meta = payload.get("metadata")
        if isinstance(meta, dict):
            dismissed = meta.get("dismissed")
            if isinstance(dismissed, list) and len(dismissed) > DISMISSED_PAYLOAD_LIMIT:
                meta["dismissed_total"] = len(dismissed)
                meta["dismissed"] = dismissed[:DISMISSED_PAYLOAD_LIMIT]
                meta["dismissed_truncated"] = True
        partial = bool(meta.get("cancelled") or meta.get("partial")) if isinstance(meta, dict) else False

        with self._write():
            cur = self._conn.execute(
                """INSERT INTO reviews
                   (target, project, score, findings_count, duration_sec,
                    tokens, cost_yuan, by_severity, llm_enabled, partial, payload)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    report.target,
                    project,
                    report.score,
                    len(report.findings),
                    round(report.duration_sec, 2),
                    report.usage.total,
                    round(report.metadata.get("cost_yuan", 0.0), 4),
                    json.dumps(report.by_severity(), ensure_ascii=False),
                    int(bool(report.llm_enabled)),
                    int(partial),
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            return int(cur.lastrowid or 0)

    def recent_reviews(
        self,
        limit: int = 10,
        project: str | None = None,
        offset: int = 0,
    ) -> list[dict]:
        where, params = self._project_where(project)
        rows = self._conn.execute(
            f"""SELECT {_REVIEW_COLUMNS} FROM reviews {where}
                ORDER BY id DESC LIMIT ? OFFSET ?""",
            (*params, limit, offset),
        ).fetchall()
        return [_review_row(r) for r in rows]

    def list_reviews(
        self,
        limit: int = 20,
        offset: int = 0,
        project: str | None = None,
        query: str | None = None,
    ) -> tuple[list[dict], int]:
        """分页查询：返回 (当页记录, 总数)。"""
        conditions: list[str] = []
        params: list[Any] = []
        canonical = _canonical_project(project)
        if canonical:
            conditions.append("project = ?")
            params.append(canonical)
        if query:
            conditions.append("(target LIKE ? OR project LIKE ?)")
            like = f"%{query}%"
            params.extend([like, like])
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        total_row = self._conn.execute(
            f"SELECT COUNT(*) AS n FROM reviews {where}", tuple(params)
        ).fetchone()
        total = int(total_row["n"]) if total_row else 0

        rows = self._conn.execute(
            f"""SELECT {_REVIEW_COLUMNS} FROM reviews {where}
                ORDER BY id DESC LIMIT ? OFFSET ?""",
            (*params, limit, offset),
        ).fetchall()
        return [_review_row(r) for r in rows], total

    def get_review_row(self, review_id: int) -> dict | None:
        row = self._conn.execute(
            f"SELECT {_REVIEW_COLUMNS} FROM reviews WHERE id = ?", (review_id,)
        ).fetchone()
        return _review_row(row) if row else None

    def get_review_findings(self, review_id: int) -> list[dict]:
        payload = self.get_review_payload(review_id)
        if payload is None:
            return []
        return payload.get("findings", [])

    def get_review_payload(self, review_id: int) -> dict | None:
        """读取一次审查的完整报告（解析后的 payload；不存在返回 None）。"""
        row = self._conn.execute(
            "SELECT payload FROM reviews WHERE id = ?", (review_id,)
        ).fetchone()
        if row is None or not row["payload"]:
            return None
        try:
            data = json.loads(row["payload"])
        except (json.JSONDecodeError, TypeError):
            return None
        return data if isinstance(data, dict) else None

    def review_count(self, project: str | None = None) -> int:
        where, params = self._project_where(project)
        row = self._conn.execute(
            f"SELECT COUNT(*) AS n FROM reviews {where}", params
        ).fetchone()
        return int(row["n"]) if row else 0

    def delete_review(self, review_id: int) -> int:
        with self._write():
            cur = self._conn.execute("DELETE FROM reviews WHERE id = ?", (review_id,))
            return int(cur.rowcount or 0)

    def purge_legacy_eval_rows(self) -> int:
        """清理评测误写入的历史记录（target 落在临时目录 codesage_eval_*）。"""
        with self._write():
            cur = self._conn.execute(
                "DELETE FROM reviews WHERE target LIKE '%codesage_eval_%'"
            )
            return int(cur.rowcount or 0)

    # ------------------------------------------------------------ 反馈

    def save_feedback(
        self,
        finding_id: str,
        verdict: str,
        category: str = "",
        file: str = "",
        title: str = "",
        note: str = "",
        project: str = "",
        mined: bool = False,
    ) -> int:
        project = _canonical_project(project) or ""
        with self._write():
            cur = self._conn.execute(
                """INSERT INTO feedback
                   (finding_id, verdict, project, category, file, title, note, mined, mined_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    finding_id,
                    verdict,
                    project,
                    category,
                    file,
                    title,
                    note,
                    int(bool(mined)),
                    "",  # 由 mark_feedback_mined 统一写入时间
                ),
            )
            return int(cur.lastrowid or 0)

    def feedback_stats(self, project: str | None = None) -> dict[str, int]:
        where, params = self._project_where(project)
        rows = self._conn.execute(
            f"SELECT verdict, COUNT(*) AS n FROM feedback {where} GROUP BY verdict",
            params,
        ).fetchall()
        return {str(r["verdict"]): int(r["n"]) for r in rows}

    def all_feedback(
        self,
        limit: int = 200,
        project: str | None = None,
        verdict: str | None = None,
        offset: int = 0,
        only_unmined: bool = False,
    ) -> list[dict]:
        conditions: list[str] = []
        params: list[Any] = []
        canonical = _canonical_project(project)
        if canonical:
            conditions.append("project = ?")
            params.append(canonical)
        if verdict:
            conditions.append("verdict = ?")
            params.append(verdict)
        if only_unmined:
            conditions.append("mined = 0")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self._conn.execute(
            f"SELECT * FROM feedback {where} ORDER BY id DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    def mark_feedback_mined(self, ids: list[int]) -> int:
        """标记反馈"已提炼"。删除经验不会回退该标记，避免下次提炼时复活。"""
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        with self._write():
            cur = self._conn.execute(
                f"UPDATE feedback SET mined = 1, mined_at = datetime('now', 'localtime') "
                f"WHERE id IN ({placeholders}) AND mined = 0",
                tuple(ids),
            )
            return int(cur.rowcount or 0)

    def reset_feedback_mined(self, project: str | None = None) -> int:
        """清除"已提炼"标记，让历史反馈可以重新提炼（用户显式操作）。"""
        canonical = _canonical_project(project)
        sql = "UPDATE feedback SET mined = 0, mined_at = '' WHERE mined = 1"
        params: tuple[Any, ...] = ()
        if canonical:
            sql += " AND project = ?"
            params = (canonical,)
        with self._write():
            cur = self._conn.execute(sql, params)
            return int(cur.rowcount or 0)

    def unmined_feedback_count(self, project: str | None = None) -> int:
        """还有多少条"可提炼但未提炼"的反馈（wrong / missed）。"""
        canonical = _canonical_project(project)
        conditions = ["mined = 0", "verdict IN ('wrong', 'missed')"]
        params: list[Any] = []
        if canonical:
            conditions.append("project = ?")
            params.append(canonical)
        row = self._conn.execute(
            f"SELECT COUNT(*) AS n FROM feedback WHERE {' AND '.join(conditions)}",
            tuple(params),
        ).fetchone()
        return int(row["n"]) if row else 0

    def feedback_count(self, project: str | None = None) -> int:
        where, params = self._project_where(project)
        row = self._conn.execute(
            f"SELECT COUNT(*) AS n FROM feedback {where}", params
        ).fetchone()
        return int(row["n"]) if row else 0

    def feedback_for(self, finding_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM feedback WHERE finding_id = ? ORDER BY id DESC",
            (finding_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ 错题本

    def add_lesson(
        self, kind: str, content: str, pattern: str = "", project: str = ""
    ) -> int:
        project = _canonical_project(project) or ""
        with self._write():
            cur = self._conn.execute(
                "INSERT INTO lessons (kind, project, pattern, content) VALUES (?, ?, ?, ?)",
                (kind, project, pattern, content),
            )
            return int(cur.lastrowid or 0)

    def lessons(
        self,
        kind: str | None = None,
        limit: int = 20,
        project: str | None = None,
        offset: int = 0,
    ) -> list[dict]:
        """查询经验。

        project 语义：
        - None → 全部；
        - 指定值 → 该项目 + 通用经验（project 为空）——通用经验对所有项目生效。
        """
        conditions: list[str] = []
        params: list[object] = []
        if kind:
            conditions.append("kind = ?")
            params.append(kind)
        canonical = _canonical_project(project)
        if canonical is not None:
            conditions.append("(project = ? OR project = '')")
            params.append(canonical)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self._conn.execute(
            f"SELECT * FROM lessons {where} ORDER BY id DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    def lesson_count(self, kind: str | None = None, project: str | None = None) -> int:
        conditions: list[str] = []
        params: list[object] = []
        if kind:
            conditions.append("kind = ?")
            params.append(kind)
        canonical = _canonical_project(project)
        if canonical is not None:
            conditions.append("(project = ? OR project = '')")
            params.append(canonical)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        row = self._conn.execute(
            f"SELECT COUNT(*) AS n FROM lessons {where}", params
        ).fetchone()
        return int(row["n"]) if row else 0

    def lesson_by_id(self, lesson_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM lessons WHERE id = ?", (lesson_id,)
        ).fetchone()
        return dict(row) if row else None

    def delete_lesson(self, lesson_id: int) -> int:
        with self._write():
            cur = self._conn.execute("DELETE FROM lessons WHERE id = ?", (lesson_id,))
            return int(cur.rowcount or 0)

    def lesson_exists(self, kind: str, content: str, project: str = "") -> bool:
        project = _canonical_project(project) or ""
        row = self._conn.execute(
            "SELECT 1 FROM lessons WHERE kind = ? AND content = ? "
            "AND project = ? LIMIT 1",
            (kind, content, project),
        ).fetchone()
        return row is not None

    # ------------------------------------------------------------ Prompt 版本

    def add_prompt_version(
        self, profile: str, version: str, template: str, note: str = ""
    ) -> None:
        row = self._conn.execute(
            "SELECT 1 FROM prompts WHERE profile = ? AND version = ? LIMIT 1",
            (profile, version),
        ).fetchone()
        if row is not None:
            return
        with self._write():
            self._conn.execute(
                "INSERT INTO prompts (profile, version, template, note) VALUES (?, ?, ?, ?)",
                (profile, version, template, note),
            )

    def prompt_versions(self, profile: str, limit: int = 20) -> list[dict]:
        rows = self._conn.execute(
            """SELECT profile, version, note, created_at
               FROM prompts WHERE profile = ? ORDER BY id DESC LIMIT ?""",
            (profile, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ 统计

    def stats_overview(self) -> dict:
        """供 GUI 顶栏/设置页使用的汇总数字。"""
        feedback = self.feedback_stats()
        lessons = self.lesson_count()
        actionable = int(feedback.get("wrong", 0)) + int(feedback.get("missed", 0))
        return {
            "reviews": self.review_count(),
            "feedback": feedback,
            "feedback_total": sum(feedback.values()),
            "lessons": lessons,
            # 近似值：可提炼的反馈条数 - 已有经验条数（提炼时按内容去重，故以计数估算）
            "pending_feedback": max(0, actionable - lessons),
        }

    # ------------------------------------------------------------ 内部

    @staticmethod
    def _project_where(project: str | None) -> tuple[str, tuple[Any, ...]]:
        canonical = _canonical_project(project)
        if canonical:
            return "WHERE project = ?", (canonical,)
        return "", ()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def _review_row(row: sqlite3.Row) -> dict:
    data = dict(row)
    raw = data.get("by_severity")
    if isinstance(raw, str) and raw:
        try:
            data["by_severity"] = json.loads(raw)
        except json.JSONDecodeError:
            data["by_severity"] = {}
    elif raw is None:
        data["by_severity"] = {}
    data["llm_enabled"] = bool(data.get("llm_enabled"))
    data["partial"] = bool(data.get("partial"))
    return data

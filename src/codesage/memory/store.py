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
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from codesage.core.models import ReviewReport


def default_store_path() -> Path:
    """用户级默认存储路径。"""
    return Path.home() / ".codesage" / "store.db"


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


class Store:
    """SQLite 存储封装。"""

    def __init__(self, db_path: Path | None = None) -> None:
        self._path = Path(db_path) if db_path else default_store_path()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """向后兼容：为旧库（无 project 列）补列。"""
        for table in ("reviews", "feedback", "lessons"):
            cols = {
                str(row[1])
                for row in self._conn.execute(f"PRAGMA table_info({table})")
            }
            if cols and "project" not in cols:
                self._conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN project TEXT DEFAULT ''"
                )

    @property
    def path(self) -> Path:
        return self._path

    # ------------------------------------------------------------ 审查历史

    def save_review(self, report: ReviewReport, project: str = "") -> int:
        cur = self._conn.execute(
            """INSERT INTO reviews
               (target, project, score, findings_count, duration_sec,
                tokens, cost_yuan, payload)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                report.target,
                project,
                report.score,
                len(report.findings),
                round(report.duration_sec, 2),
                report.usage.total,
                round(report.metadata.get("cost_yuan", 0.0), 4),
                json.dumps(report.to_dict(), ensure_ascii=False),
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid or 0)

    def recent_reviews(
        self, limit: int = 10, project: str | None = None
    ) -> list[dict]:
        if project is not None:
            rows = self._conn.execute(
                """SELECT id, target, project, score, findings_count, duration_sec,
                          tokens, cost_yuan, created_at
                   FROM reviews WHERE project = ?
                   ORDER BY id DESC LIMIT ?""",
                (project, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """SELECT id, target, project, score, findings_count, duration_sec,
                          tokens, cost_yuan, created_at
                   FROM reviews ORDER BY id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

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

    def review_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM reviews").fetchone()
        return int(row["n"]) if row else 0

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
    ) -> int:
        cur = self._conn.execute(
            """INSERT INTO feedback
               (finding_id, verdict, project, category, file, title, note)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (finding_id, verdict, project, category, file, title, note),
        )
        self._conn.commit()
        return int(cur.lastrowid or 0)

    def feedback_stats(self, project: str | None = None) -> dict[str, int]:
        if project is not None:
            rows = self._conn.execute(
                """SELECT verdict, COUNT(*) AS n FROM feedback
                   WHERE project = ? GROUP BY verdict""",
                (project,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT verdict, COUNT(*) AS n FROM feedback GROUP BY verdict"
            ).fetchall()
        return {str(r["verdict"]): int(r["n"]) for r in rows}

    def all_feedback(self, limit: int = 200) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM feedback ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ 错题本

    def add_lesson(
        self, kind: str, content: str, pattern: str = "", project: str = ""
    ) -> int:
        cur = self._conn.execute(
            "INSERT INTO lessons (kind, project, pattern, content) VALUES (?, ?, ?, ?)",
            (kind, project, pattern, content),
        )
        self._conn.commit()
        return int(cur.lastrowid or 0)

    def lessons(
        self,
        kind: str | None = None,
        limit: int = 20,
        project: str | None = None,
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
        if project is not None:
            conditions.append("(project = ? OR project = '')")
            params.append(project)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self._conn.execute(
            f"SELECT * FROM lessons {where} ORDER BY id DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def lesson_exists(self, kind: str, content: str, project: str = "") -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM lessons WHERE kind = ? AND content = ? AND project = ? LIMIT 1",
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
        self._conn.execute(
            "INSERT INTO prompts (profile, version, template, note) VALUES (?, ?, ?, ?)",
            (profile, version, template, note),
        )
        self._conn.commit()

    def prompt_versions(self, profile: str, limit: int = 20) -> list[dict]:
        rows = self._conn.execute(
            """SELECT profile, version, note, created_at
               FROM prompts WHERE profile = ? ORDER BY id DESC LIMIT ?""",
            (profile, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        self._conn.close()

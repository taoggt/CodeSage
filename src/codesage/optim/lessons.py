"""错题本：误报/漏报模式沉淀，注入 Prompt 形成自优化闭环。

闭环逻辑：
    人工反馈（feedback 表）
        → mine_from_feedback() 提炼为 Lesson（lessons 表）
        → few_shot_block() 生成经验文本
        → 注入 system prompt（agent/profiles.build_system_prompt 的 lessons_block）

防过拟合纪律（面试要点）：
- 注入的经验条数有上限（few_shot_block 的 limit）；
- 效果必须通过 Harness A/B 评测验证（不能"感觉变好了"）；
- 评测数据集拆分调优集/验证集（见 README 的评测章节）。
"""

from __future__ import annotations

from dataclasses import dataclass

from codesage.memory.store import Store

LESSON_FALSE_POSITIVE = "false_positive"
LESSON_FALSE_NEGATIVE = "false_negative"

MAX_BLOCK_CHARS = 1200  # 经验块的字符上限（防止 prompt 膨胀）


@dataclass
class Lesson:
    """一条经验。"""

    id: int
    kind: str
    pattern: str
    content: str
    hits: int = 0


class LessonBook:
    """错题本。"""

    def __init__(self, store: Store) -> None:
        self._store = store

    def add(self, kind: str, content: str, pattern: str = "", project: str = "") -> int:
        """添加经验（按 kind+content+project 去重）。返回记录 ID（已存在返回 -1）。

        project 为审查根目录的绝对路径；空字符串表示通用经验（对所有项目生效）。
        """
        if self._store.lesson_exists(kind, content, project):
            return -1
        return self._store.add_lesson(
            kind=kind, content=content, pattern=pattern, project=project
        )

    def all(self, limit: int = 20, project: str | None = None) -> list[Lesson]:
        rows = self._store.lessons(limit=limit, project=project)
        return [
            Lesson(
                id=int(r["id"]),
                kind=str(r["kind"]),
                pattern=str(r.get("pattern") or ""),
                content=str(r["content"]),
                hits=int(r.get("hits") or 0),
            )
            for r in rows
        ]

    def mine_from_feedback(self) -> dict[str, int]:
        """从反馈中提炼经验。返回 {"false_positive": n, "false_negative": m}。

        每条反馈自带 project（记录反馈时所在的项目），提炼时保持项目隔离。
        """
        added = {"false_positive": 0, "false_negative": 0}
        for fb in self._store.all_feedback(limit=500):
            verdict = str(fb.get("verdict", ""))
            project = str(fb.get("project") or "")
            if verdict == "wrong":
                content = (
                    f"【误报案例】{fb.get('file', '?')} "
                    f"{fb.get('title', '?')}"
                    + (f"：{fb.get('note')}" if fb.get("note") else "")
                )
                if self.add(
                    LESSON_FALSE_POSITIVE, content, str(fb.get("category") or ""), project
                ) > 0:
                    added["false_positive"] += 1
            elif verdict == "missed":
                content = (
                    f"【漏报案例】{fb.get('file', '?')} "
                    f"{fb.get('title', '?')}"
                    + (f"：{fb.get('note')}" if fb.get("note") else "")
                )
                if self.add(
                    LESSON_FALSE_NEGATIVE, content, str(fb.get("category") or ""), project
                ) > 0:
                    added["false_negative"] += 1
        return added

    def few_shot_block(self, limit: int = 6, project: str | None = None) -> str:
        """生成注入 prompt 的经验块（该项目 + 通用经验，控制条数与总字符数）。"""
        lessons = self.all(limit=limit, project=project)
        if not lessons:
            return ""
        lines: list[str] = []
        used = 0
        for lesson in lessons:
            prefix = "误报" if lesson.kind == LESSON_FALSE_POSITIVE else "漏报"
            line = f"- [{prefix}] {lesson.content}"
            if used + len(line) > MAX_BLOCK_CHARS:
                break
            lines.append(line)
            used += len(line)
        return "\n".join(lines)

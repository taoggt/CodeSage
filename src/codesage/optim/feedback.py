"""反馈采集：把人工判定（误报/确认/漏报）写入存储。

闭环的第一环：CLI 的 `codesage feedback` 命令调用这里，
数据进入 store 后由 LessonBook 提炼为经验（错题本）。
"""

from __future__ import annotations

from dataclasses import dataclass

from codesage.memory.store import Store

VALID_VERDICTS = ("wrong", "right", "missed")

_VERDICT_ZH = {
    "wrong": "误报（判定有误）",
    "right": "确认（确实有问题）",
    "missed": "漏报（应该发现但没发现）",
}


@dataclass
class FeedbackEntry:
    """一条人工反馈。"""

    finding_id: str
    verdict: str  # wrong / right / missed
    project: str = ""   # 审查根目录绝对路径（错题本按项目隔离的依据）
    category: str = ""
    file: str = ""
    title: str = ""
    note: str = ""


def record_feedback(store: Store, entry: FeedbackEntry) -> int:
    """写入一条反馈，返回记录 ID。非法 verdict 抛 ValueError。"""
    if entry.verdict not in VALID_VERDICTS:
        raise ValueError(
            f"非法 verdict '{entry.verdict}'，可用值：{', '.join(VALID_VERDICTS)}"
        )
    return store.save_feedback(
        finding_id=entry.finding_id,
        verdict=entry.verdict,
        category=entry.category,
        file=entry.file,
        title=entry.title,
        note=entry.note,
        project=entry.project,
    )


def verdict_label(verdict: str) -> str:
    """展示用中文标签。"""
    return _VERDICT_ZH.get(verdict, verdict)

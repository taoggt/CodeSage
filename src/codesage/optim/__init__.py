"""自优化层：反馈采集 → 错题本 → Prompt 版本管理（自优化闭环的三块拼图）。"""

from codesage.optim.feedback import FeedbackEntry, record_feedback
from codesage.optim.lessons import Lesson, LessonBook
from codesage.optim.prompts import PromptRegistry

__all__ = [
    "FeedbackEntry",
    "record_feedback",
    "Lesson",
    "LessonBook",
    "PromptRegistry",
]

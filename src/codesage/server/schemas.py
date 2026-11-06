"""请求模型（响应统一用 dict：报告的字段形状由 ReviewReport.to_dict 决定）。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Verdict = Literal["wrong", "right", "missed"]
LessonKind = Literal["false_positive", "false_negative"]
Profile = Literal["full", "security", "performance"]


class OpenWorkspaceRequest(BaseModel):
    path: str


class PickDirectoryRequest(BaseModel):
    initial: str | None = None


class ReviewJobRequest(BaseModel):
    target: str
    profile: Profile = "full"
    use_llm: bool = True
    changed_only: bool = False
    max_turns: int | None = Field(default=None, ge=1, le=50)
    keep_partial: bool = True
    # 语言范围：None = 沿用配置（默认 auto）。取值同 CLI --language。
    languages: str | None = None


class FeedbackRequest(BaseModel):
    finding_id: str
    verdict: Verdict
    note: str = ""
    review_id: int | None = None
    auto_mine: bool = True


class LessonCreateRequest(BaseModel):
    kind: LessonKind
    content: str
    pattern: str = ""
    project: str = ""


class MissedLessonRequest(BaseModel):
    file: str = ""
    line: int | None = None
    title: str
    category: str = ""
    note: str = ""
    project: str = ""


class MineRequest(BaseModel):
    project: str | None = None


class EvalJobRequest(BaseModel):
    dataset: str
    tag: str = "baseline"
    mock: bool = False
    sample: int | None = Field(default=None, ge=1, le=200)
    profile: Profile = "full"
    results_dir: str = ".codesage-eval"


class PurgeRequest(BaseModel):
    kind: Literal["eval"] = "eval"

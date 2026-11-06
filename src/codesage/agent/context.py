"""初始上下文组装：把扫描结果 + 候选发现变成任务简报。

Token 控制的第一道闸门：
- 文件清单只给路径与行数（不给内容，让模型按需 read_file）；
- 候选发现按严重级排序并限制条数（max_candidates）；
- 分块策略提示（大文件怎么读）。
"""

from __future__ import annotations

from codesage.core.languages import LANGUAGE_SPECS
from codesage.core.models import Finding


def build_file_inventory(files: list[tuple[str, int]]) -> str:
    """文件清单（路径 | 行数），限制展示数量避免占满上下文。"""
    if not files:
        return "（无文件）"
    lines = [f"- {path} | {lines} 行" for path, lines in files]
    return "\n".join(lines)


def describe_scope(files: list[tuple[str, int]], languages: dict[str, int] | None) -> str:
    """描述本次审查范围，如 "Python 文件 3 个、Java 文件 2 个"。

    为什么要按语言拆开计数：模型看到"3 个文件"却读到 Java 代码时，
    容易套用 Python 的惯例给建议（例如让 Java 方法写 docstring）。
    语言清单本身是最便宜的先验。
    """
    if not languages:
        return f"Python 文件 {len(files)} 个"

    ordered = [
        (spec.display, languages[lang.value])
        for lang, spec in LANGUAGE_SPECS.items()
        if languages.get(lang.value)
    ]
    if not ordered:
        return f"代码文件 {len(files)} 个"
    if len(ordered) == 1:
        display, count = ordered[0]
        return f"{display} 文件 {count} 个"
    detail = "、".join(f"{display} {count} 个" for display, count in ordered)
    return f"文件 {len(files)} 个（{detail}）"


def build_candidates_block(
    candidates: list[Finding],
    max_candidates: int = 40,
) -> str:
    """候选发现清单（供模型复核）。"""
    if not candidates:
        return "（无候选发现——请独立审查全部文件）"

    order = {"critical": 0, "major": 1, "minor": 2, "info": 3}
    sorted_candidates = sorted(
        candidates,
        key=lambda f: (order[f.severity.value], f.file, f.line_start),
    )
    shown = sorted_candidates[:max_candidates]
    lines = []
    for idx, f in enumerate(shown, start=1):
        lines.append(
            f"{idx}. [{f.severity.label}][{f.category.value}] "
            f"{f.file}:{f.line_start} [{f.rule_id or f.source.value}] {f.title}"
        )
    if len(sorted_candidates) > max_candidates:
        lines.append(
            f"...（另有 {len(sorted_candidates) - max_candidates} 条低优先级候选未列出）"
        )
    return "\n".join(lines)


def build_task_brief(
    *,
    target: str,
    files: list[tuple[str, int]],
    candidates: list[Finding],
    max_candidates: int = 40,
    changed_only: bool = False,
    languages: dict[str, int] | None = None,
) -> str:
    """生成初始 user 消息（任务简报）。

    languages 为语言 → 文件数（pipeline 的探测结果）；None 时退回 v1 的
    "Python 文件 N 个"措辞，保证历史评测的 Prompt 不变。
    """
    scope_note = (
        "本次为**增量审查**：只关注 git 变更涉及的文件与行区间附近的代码。"
        if changed_only
        else "本次为**全量审查**。"
    )
    return f"""## 审查任务

目标：{target}（{describe_scope(files, languages)}）
{scope_note}

## 文件清单（相对路径 | 行数）

{build_file_inventory(files)}

## 确定性分析候选发现（共 {len(candidates)} 条，需复核）

{build_candidates_block(candidates, max_candidates)}

## 建议的工作流程

1. 复核候选发现：读取相关代码确认属实性（属实→submit_finding 重新提交并附修复建议；误报→忽略）。
2. 独立审查文件：优先审查未被候选覆盖的文件与核心逻辑（工具、公共接口优先）。
3. 全部完成后，输出一段简短总结（不再调用工具）结束任务。
"""

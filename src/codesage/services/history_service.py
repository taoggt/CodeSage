"""历史记录服务：分页、详情回放、两次审查对比、清理。

CLI 的 `history` 命令与 GUI 共用这一层，避免"两份真相"。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from codesage.memory.store import Store


def normalize_project(project: Path | str) -> str:
    """归一化项目参数，与写入端 project 语义保持一致。

    写入端（pipeline）的 project = 审查根目录：单文件审查时取文件所在目录，
    因此查询端传文件路径时必须同样归一到父目录，否则精确匹配永远失败。
    判定规则：存在的文件 → 父目录；带后缀的不存在路径（文件已删除）→ 父目录；
    其余（目录）→ 原样。
    """
    resolved = Path(project).resolve()
    if resolved.is_file() or (not resolved.is_dir() and resolved.suffix):
        return str(resolved.parent)
    return str(resolved)


def display_target(target: str, base: Path | None = None) -> str:
    """目标路径的相对化展示（在当前目录下时显示相对路径）。"""
    try:
        return Path(target).resolve().relative_to((base or Path.cwd()).resolve()).as_posix()
    except (ValueError, OSError):
        return target


def list_reviews(
    store: Store,
    limit: int = 20,
    offset: int = 0,
    project: str | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    project_key = normalize_project(project) if project else None
    items, total = store.list_reviews(
        limit=limit, offset=offset, project=project_key, query=query
    )
    for item in items:
        item["target_display"] = display_target(str(item.get("target") or ""))
        item["project_name"] = Path(str(item.get("project") or "")).name
    return {"total": total, "limit": limit, "offset": offset, "items": items}


def get_detail(store: Store, review_id: int) -> dict[str, Any] | None:
    """完整报告 + 该报告内各 finding 的反馈状态。"""
    payload = store.get_review_payload(review_id)
    if payload is None:
        return None
    row = store.get_review_row(review_id) or {}

    feedback_by_finding: dict[str, list[dict]] = {}
    for finding in payload.get("findings") or []:
        finding_id = str(finding.get("id") or "")
        if not finding_id:
            continue
        items = store.feedback_for(finding_id)
        if items:
            feedback_by_finding[finding_id] = [
                {
                    "id": item["id"],
                    "verdict": item["verdict"],
                    "note": item.get("note") or "",
                    "created_at": item.get("created_at") or "",
                }
                for item in items
            ]

    payload["feedback_by_finding"] = feedback_by_finding
    payload["review"] = {
        "id": review_id,
        "project": row.get("project") or "",
        "created_at": row.get("created_at") or "",
        "llm_enabled": bool(row.get("llm_enabled")),
        "partial": bool(row.get("partial")),
    }
    return payload


def _finding_key(finding: dict) -> tuple[str, int, str]:
    return (
        str(finding.get("file") or ""),
        int(finding.get("line_start") or 0),
        str(finding.get("title") or ""),
    )


def compare_reviews(store: Store, before_id: int, after_id: int) -> dict[str, Any]:
    """对比两次审查：新增 / 已修复 / 持续存在 + 评分变化。"""
    before = store.get_review_payload(before_id)
    after = store.get_review_payload(after_id)
    if before is None or after is None:
        raise ValueError("找不到指定的审查记录")

    before_items = before.get("findings") or []
    after_items = after.get("findings") or []
    before_map = {_finding_key(f): f for f in before_items}
    after_map = {_finding_key(f): f for f in after_items}

    added = [after_map[k] for k in after_map.keys() - before_map.keys()]
    resolved = [before_map[k] for k in before_map.keys() - after_map.keys()]
    persisted = [after_map[k] for k in after_map.keys() & before_map.keys()]

    def brief(findings: list[dict]) -> list[dict]:
        return [
            {
                "file": f.get("file"),
                "line_start": f.get("line_start"),
                "title": f.get("title"),
                "severity": f.get("severity"),
                "id": f.get("id"),
            }
            for f in findings
        ]

    return {
        "before": {"id": before_id, "score": before.get("score")},
        "after": {"id": after_id, "score": after.get("score")},
        "score_delta": int(after.get("score") or 0) - int(before.get("score") or 0),
        "added": brief(added),
        "resolved": brief(resolved),
        "persisted": brief(persisted),
        "counts": {
            "added": len(added),
            "resolved": len(resolved),
            "persisted": len(persisted),
        },
    }


def feedback_stats(store: Store, project: str | None = None) -> dict[str, int]:
    return store.feedback_stats(project=normalize_project(project) if project else None)


def delete_review(store: Store, review_id: int) -> int:
    return store.delete_review(review_id)


def purge_eval_rows(store: Store) -> int:
    """清理历史上被评测误写入的临时记录。"""
    return store.purge_legacy_eval_rows()

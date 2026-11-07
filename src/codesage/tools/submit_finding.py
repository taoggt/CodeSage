"""submit_finding 工具：LLM 通过工具调用提交结构化发现。

这是本项目的一个关键设计：
- 让模型用 function calling 提交结构化数据，而不是输出自由文本再解析
  （自由文本解析是脆弱且不可靠的）；
- 参数校验失败时返回 ERROR 文本，模型会自动修正重试（自我纠正）；
- 事实校验（路径 / 行号 / 引文）在服务端确定性完成，不信任模型自述。

事实约束（2026-11 审计后补上）：
- file 必须解析到工作区内且真实存在，防止幻觉路径与路径穿越；
- line_start / line_end 不得超过文件实际行数；
- evidence 是可选引文：给了就与真实代码做子串比对，不符则打回；
  没给则把置信度封顶到 UNVERIFIED_CONFIDENCE_CAP——不阻断流程，
  但让「无证据的结论」在报告里可识别、可被下游按置信度过滤。
- 判重按「文件 + 相近行 + 归一化标题」，不含 category；同位置标题
  不同的两条并报而不是静默吞掉，并把已有标题回喂给模型供其自纠。
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from codesage.agent.registry import tool
from codesage.core.models import (
    NEAR_LINE_WINDOW,
    Category,
    Finding,
    Severity,
    Source,
    same_issue,
)
from codesage.tools.paths import normalize_relative, resolve_inside

_CATEGORY_OPTIONS = ", ".join(c.value for c in Category)
_SEVERITY_OPTIONS = ", ".join(s.value for s in Severity)

# 未提供证据引文时，置信度最多只能到 0.7（模型自评不可全信）
UNVERIFIED_CONFIDENCE_CAP = 0.7
_MIN_EVIDENCE_CHARS = 4  # 太短的片段没有辨识度，不足以作为引文


def _normalize_escaped_newlines(text: str) -> str:
    """还原模型偶尔输出的字面转义序列（\\n / \\r\\n）。

    背景：部分模型通过 function calling 提交多行文本时会双重转义——
    JSON 解析后得到的不是真实换行，而是字面 "\\n" 两个字符，
    终端与报告中会显示成一串 \\n，严重影响可读性。

    保守策略（避免误伤代码内容）：
    - 文本已含真实换行 → 不处理（无法区分代码里合法的 "\\n" 字面量）；
    - 字面 "\\n" 出现次数 < 2 → 不处理（单个通常是代码示例中的
      字符串字面量，如 print("a\\nb")）；
    - 其余情况视为双重转义错误，统一还原为真实换行。
    """
    if "\n" in text or "\r" in text:
        return text
    if text.count("\\n") < 2:
        return text
    return text.replace("\\r\\n", "\n").replace("\\n", "\n")


def _evidence_matches(lines: list[str], start: int, end: int, evidence: str) -> bool:
    """引文是否能在目标行附近（±NEAR_LINE_WINDOW）的真实代码里找到。

    比对时去掉所有空白字符：模型复制代码常带上行号前缀、缩进差异，
    逐字符精确匹配会误伤。要求片段至少 _MIN_EVIDENCE_CHARS 个字符，
    否则 `x = 1` 这种短串到处都能命中，起不到 grounding 作用。
    """
    lo = max(0, start - 1 - NEAR_LINE_WINDOW)
    hi = min(len(lines), end + NEAR_LINE_WINDOW)
    haystack = "".join("".join(line.split()) for line in lines[lo:hi])
    if not haystack:
        return False
    for segment in evidence.splitlines():
        needle = "".join(segment.split())
        if len(needle) >= _MIN_EVIDENCE_CHARS and needle in haystack:
            return True
    return False


def _actual_lines(lines: list[str], start: int, end: int) -> str:
    """把目标行附近的真实代码回给模型，让它能就地纠正幻觉。"""
    lo = max(0, start - 1 - NEAR_LINE_WINDOW)
    hi = min(len(lines), end + NEAR_LINE_WINDOW)
    width = len(str(hi))
    return "\n".join(f"{i + 1:>{width}} | {lines[i]}" for i in range(lo, hi))[:600]


def make_submit_finding_tool(
    collector: list[Finding],
    workspace: Path,
) -> Callable[..., str]:
    """创建 submit_finding 工具，发现写入 collector 列表。

    workspace 是事实校验的根：所有提交都必须落到工作区内的真实文件、
    真实行号上。旧版收了这个参数却从未使用，幻觉路径照单全收。
    """
    root = Path(workspace).resolve()

    @tool(name="submit_finding")
    def submit_finding(
        file: str,
        line_start: int,
        line_end: int,
        category: str,
        severity: str,
        title: str,
        description: str = "",
        suggestion: str = "",
        confidence: float = 0.8,
        evidence: str = "",
    ) -> str:
        """提交一条审查发现（结构化记录到最终报告）。

        Args:
            file: 相对工作区根目录的文件路径（必须是文件清单里的真实文件）
            line_start: 问题起始行号（1-based，必须来自 read_file 的实际行号）
            line_end: 问题结束行号（单行问题与 line_start 相同）
            category: 类别（quality / bug / performance / security / best_practice）
            severity: 严重级（critical / major / minor / info）
            title: 一句话标题（如 “未校验的 SQL 拼接”）
            description: 问题详细说明与触发条件（多行文本请使用真实换行符分段）
            suggestion: 具体修复建议（可包含修改后的代码，多行文本请使用真实换行符分段）
            confidence: 置信度 0~1（证据充分给 0.9+，启发式判断给 0.6~0.8）
            evidence: 支撑结论的原始代码行，从 read_file 逐字复制；系统会与真实文件比对，不提供则置信度封顶 0.7
        """
        # ---- 枚举校验（失败回填 ERROR，模型会自行修正）
        try:
            cat = Category(str(category).strip().lower())
        except ValueError:
            return f"ERROR: 非法 category '{category}'。可用值：{_CATEGORY_OPTIONS}"
        try:
            sev = Severity(str(severity).strip().lower())
        except ValueError:
            return f"ERROR: 非法 severity '{severity}'。可用值：{_SEVERITY_OPTIONS}"

        try:
            start = int(line_start)
            end = int(line_end)
        except (TypeError, ValueError):
            return "ERROR: line_start / line_end 必须是整数行号"
        if start < 1 or end < 1:
            return "ERROR: 行号必须 ≥ 1（1-based）"
        end = max(start, end)

        # ---- 事实校验：路径 / 文件存在 / 行号上界
        raw_path = str(file).strip()
        target = resolve_inside(root, raw_path)
        if target is None:
            return f"ERROR: 非法路径（必须是工作区内的文件）：{raw_path}"
        if not target.is_file():
            return f"ERROR: 文件不存在：{raw_path}（请先 read_file 确认路径）"
        rel = normalize_relative(target, root)
        try:
            lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            return f"ERROR: 读取 {rel} 失败：{exc}"

        total = len(lines)
        if total == 0:
            return f"ERROR: {rel} 是空文件，无法定位行号"
        if start > total:
            return (
                f"ERROR: line_start={start} 超过 {rel} 的总行数（{total}）。"
                f"{rel} 附近实际内容：\n{_actual_lines(lines, total, total)}"
            )

        notes: list[str] = []
        if end > total:
            notes.append(f"line_end 已由 {end} 收敛到文件末行 {total}")
            end = total

        # ---- 可选引文：给了就必须是真的，没给就压置信度
        # 只去首尾空行，保留引文原有的缩进（比对时本来就会去掉空白，
        # 但落库展示时保留缩进更接近 read_file 的原文）。
        evidence_text = _normalize_escaped_newlines(str(evidence)).strip("\n")
        if evidence_text:
            if not _evidence_matches(lines, start, end, evidence_text):
                return (
                    f"ERROR: evidence 引文与 {rel}:{start} 附近的实际代码不符，"
                    "请重新 read_file 后逐字复制原文再提交。实际内容：\n"
                    f"{_actual_lines(lines, start, end)}"
                )
        else:
            notes.append("未提供 evidence 引文")

        title_text = str(title).strip()
        if not title_text:
            return "ERROR: title 不能为空"

        try:
            conf = min(1.0, max(0.0, float(confidence)))
        except (TypeError, ValueError):
            conf = 0.8
        if not evidence_text:
            conf = min(conf, UNVERIFIED_CONFIDENCE_CAP)
            notes.append(f"置信度封顶 {UNVERIFIED_CONFIDENCE_CAP}")

        finding = Finding(
            file=rel,
            line_start=start,
            line_end=end,
            category=cat,
            severity=sev,
            title=title_text[:200],
            description=_normalize_escaped_newlines(str(description).strip()),
            suggestion=_normalize_escaped_newlines(str(suggestion).strip()),
            evidence=evidence_text,
            confidence=conf,
            source=Source.LLM,
            rule_id="CG-LLM",
        )

        # ---- 真重复：同位置 + 同标题（与 category 无关）→ 保留高置信度
        for idx, existing in enumerate(collector):
            if same_issue(existing, finding):
                if finding.confidence > existing.confidence:
                    collector[idx] = finding
                    return (
                        f"已更新同位置的同一条发现（去重后共 {len(collector)} 条）："
                        f"{finding.title}"
                    )
                return (
                    "同位置已有置信度更高的同一条发现，忽略本条"
                    f"（共 {len(collector)} 条）：{existing.title}"
                )

        # ---- 同位置但标题不同：两条独立问题，并报；把已有标题回喂给模型，
        #      让「其实是重复、只是改了类别/措辞」由模型自己再判一次。
        nearby = [
            e.title
            for e in collector
            if e.file == rel
            and abs(e.line_start - start) <= NEAR_LINE_WINDOW
            and not same_issue(e, finding)
        ]
        collector.append(finding)
        reply = f"已记录发现 #{len(collector)}：{finding.title}（当前共 {len(collector)} 条）"
        if nearby:
            reply += (
                f"。注意：同位置已有 {'；'.join(nearby[:3])}。"
                "若与它们是同一问题请勿重复提交；若确为不同问题请保留，"
                "并在标题中体现差异"
            )
        if notes:
            reply += f"（{'；'.join(notes)}）"
        return reply

    return submit_finding

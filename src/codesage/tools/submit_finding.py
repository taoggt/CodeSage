"""submit_finding 工具：LLM 通过工具调用提交结构化发现。

这是本项目的一个关键设计：
- 让模型用 function calling 提交结构化数据，而不是输出自由文本再解析
  （自由文本解析是脆弱且不可靠的）；
- 参数校验失败时返回 ERROR 文本，模型会自动修正重试（自我纠正）；
- 同位置去重：保留置信度更高的版本。
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from codesage.agent.registry import tool
from codesage.core.models import Category, Finding, Severity, Source

_CATEGORY_OPTIONS = ", ".join(c.value for c in Category)
_SEVERITY_OPTIONS = ", ".join(s.value for s in Severity)


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


def make_submit_finding_tool(
    collector: list[Finding],
    workspace: Path,
) -> Callable[..., str]:
    """创建 submit_finding 工具，发现写入 collector 列表。"""

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
    ) -> str:
        """提交一条审查发现（结构化记录到最终报告）。

        Args:
            file: 相对工作区根目录的文件路径
            line_start: 问题起始行号（1-based，必须来自 read_file 的实际行号）
            line_end: 问题结束行号（单行问题与 line_start 相同）
            category: 类别（quality / bug / performance / security / best_practice）
            severity: 严重级（critical / major / minor / info）
            title: 一句话标题（如 “未校验的 SQL 拼接”）
            description: 问题详细说明与触发条件（多行文本请使用真实换行符分段）
            suggestion: 具体修复建议（可包含修改后的代码，多行文本请使用真实换行符分段）
            confidence: 置信度 0~1（证据充分给 0.9+，启发式判断给 0.6~0.8）
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

        title_text = str(title).strip()
        if not title_text:
            return "ERROR: title 不能为空"

        try:
            conf = min(1.0, max(0.0, float(confidence)))
        except (TypeError, ValueError):
            conf = 0.8

        file_text = str(file).strip().lstrip("./")
        finding = Finding(
            file=file_text,
            line_start=start,
            line_end=end,
            category=cat,
            severity=sev,
            title=title_text[:200],
            description=_normalize_escaped_newlines(str(description).strip()),
            suggestion=_normalize_escaped_newlines(str(suggestion).strip()),
            confidence=conf,
            source=Source.LLM,
            rule_id="CG-LLM",
        )

        # ---- 同位置去重：保留置信度更高的一条
        for idx, existing in enumerate(collector):
            if existing.dedup_key == finding.dedup_key:
                if finding.confidence > existing.confidence:
                    collector[idx] = finding
                    return f"已更新同位置发现（去重后共 {len(collector)} 条）：{finding.title}"
                return f"同位置已有更高置信度的发现，忽略本条（共 {len(collector)} 条）"

        collector.append(finding)
        return f"已记录发现 #{len(collector)}：{finding.title}（当前共 {len(collector)} 条）"

    return submit_finding

"""核心领域模型：Finding / ReviewReport / TokenUsage。

设计约定（解耦的关键）：
- 所有模块之间传递的数据都使用这里的模型：
  分析层（AST 规则 / Ruff / Bandit）与 Agent 层互不认识，
  但产出的都是同一个 Finding，报告层只认 Finding。
- Finding 是唯一的“问题”载体，无论来源是规则还是 LLM。
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from enum import Enum


class Severity(str, Enum):
    """问题严重级（同时对应报告中的 P0-P3 标签）。"""

    CRITICAL = "critical"  # P0：安全漏洞 / 必然崩溃
    MAJOR = "major"        # P1：明确 bug / 严重性能问题
    MINOR = "minor"        # P2：代码质量 / 可维护性
    INFO = "info"          # P3：风格建议 / 最佳实践

    @property
    def label(self) -> str:
        return {
            "critical": "P0",
            "major": "P1",
            "minor": "P2",
            "info": "P3",
        }[self.value]

    @property
    def weight(self) -> int:
        """评分扣分权重（报告总分 100 分起步）。"""
        return {
            "critical": 15,
            "major": 8,
            "minor": 3,
            "info": 1,
        }[self.value]

    @property
    def zh(self) -> str:
        # 用 .get 兜底而非直接索引：枚举是增量扩展的，漏配中文名不应该让
        # 整份报告渲染崩掉（旧实现是 dict[...] 直索引 → KeyError）。
        return {
            "critical": "严重",
            "major": "重要",
            "minor": "一般",
            "info": "提示",
        }.get(self.value, self.value)


class Category(str, Enum):
    """问题类别（对应五大审查功能）。"""

    QUALITY = "quality"                # 代码质量（风格/命名/注释）
    BUG = "bug"                        # 潜在 bug（逻辑/边界/资源泄漏）
    PERFORMANCE = "performance"        # 性能优化
    SECURITY = "security"              # 安全漏洞
    BEST_PRACTICE = "best_practice"    # 最佳实践

    @property
    def zh(self) -> str:
        return {
            "quality": "代码质量",
            "bug": "潜在 Bug",
            "performance": "性能",
            "security": "安全",
            "best_practice": "最佳实践",
        }.get(self.value, self.value)


class Source(str, Enum):
    """问题来源（决定置信度语义与展示方式）。"""

    AST_RULE = "ast_rule"  # 自研 AST 规则（确定性，Python）
    JAVA_RULE = "java_rule"  # 自研 Java 规则 + 复杂度指标（确定性，Java）
    RUFF = "ruff"          # Ruff 静态检查
    BANDIT = "bandit"      # Bandit 安全扫描
    RADON = "radon"        # radon 复杂度分析
    LLM = "llm"            # LLM 语义确认 / 补充发现

    @property
    def zh(self) -> str:
        # .get 兜底：新增来源漏配中文名时退化为枚举值，而不是 KeyError 崩报告
        return {
            "ast_rule": "规则",
            "java_rule": "Java 规则",
            "ruff": "Ruff",
            "bandit": "Bandit",
            "radon": "radon",
            "llm": "LLM",
        }.get(self.value, self.value)


def coerce_source(raw: object) -> Source:
    """把任意 raw 值收敛为 Source。

    容错的意义：数据库里的 payload 是跨版本共享的——旧版 GUI 可能读到新版写入的
    `java_rule`。直接 `Source(value)` 会抛 ValueError 让整个历史页白屏，
    而降级成 AST_RULE 至少还能展示（来源徽章不准，但信息不丢）。
    """
    if isinstance(raw, Source):
        return raw
    try:
        return Source(str(raw))
    except ValueError:
        return Source.AST_RULE


def resolve_language_code(explicit: object, file_path: object = "") -> str:
    """解析语言代码：显式值 → 按文件名后缀推断 → 回退 python。"""
    from codesage.core.languages import (
        Language,
        all_specs,
        language_spec_for_path,
    )

    value = str(explicit or "").strip().lower()
    if value in {spec.code for spec in all_specs()}:
        return value
    spec = language_spec_for_path(str(file_path or ""))
    return spec.code if spec is not None else Language.PYTHON.value


@dataclass
class Finding:
    """一条审查发现。

    字段说明：
    - file: 相对工作区根目录的 POSIX 路径
    - line_start / line_end: 1-based 行号范围
    - confidence: 置信度 0~1（规则类通常 0.6~1.0；LLM 确认后由模型给出）
    - source + rule_id: 溯源信息（报告里展示“由谁发现”）
    - language: 所属语言代码（python / java）。空串表示"未标注"，
      读取时按 file 后缀推断——历史 payload 没有这个字段，不能因此崩。
      为什么放在 Finding 而不是 ReviewReport：一次审查可能是混合仓库，
      报告级单一语言字段会让导出围栏与逐文件高亮全部失效。
    """

    file: str
    line_start: int
    line_end: int
    category: Category
    severity: Severity
    title: str
    description: str = ""
    suggestion: str = ""
    evidence: str = ""
    confidence: float = 1.0
    source: Source = Source.AST_RULE
    rule_id: str = ""
    language: str = ""

    @property
    def id(self) -> str:
        """稳定的短 ID（用于 CLI 反馈与报告引用）。

        使用 SHA1 仅为生成短标识（非安全用途），
        usedforsecurity=False 明确声明这一点（同时消除 Bandit B324 告警）。
        """
        key = f"{self.file}:{self.line_start}:{self.rule_id or self.title}:{self.source.value}"
        return hashlib.sha1(key.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]

    @property
    def dedup_key(self) -> str:
        """去重键：同文件 + 相近行 + 同类别视为同一问题（保留高置信度者）。"""
        return f"{self.file}:{self.line_start // 3}:{self.category.value}"

    @property
    def language_code(self) -> str:
        """解析后的语言代码：显式字段优先，其次按文件名后缀推断，最后回退 python。

        为什么需要三层：LLM 提交的 finding 不会填 language（它只看得到代码），
        历史 payload 完全没有这个字段，而导出围栏/前端高亮都需要一个确定值。
        """
        return resolve_language_code(self.language, self.file)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["category"] = self.category.value
        d["severity"] = self.severity.value
        d["source"] = self.source.value
        d["language"] = self.language_code
        d["id"] = self.id
        d["severity_label"] = self.severity.label
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Finding:
        return cls(
            file=d["file"],
            line_start=int(d["line_start"]),
            line_end=int(d.get("line_end", d["line_start"])),
            category=Category(d["category"]),
            severity=Severity(d["severity"]),
            title=d["title"],
            description=d.get("description", ""),
            suggestion=d.get("suggestion", ""),
            evidence=d.get("evidence", ""),
            confidence=float(d.get("confidence", 1.0)),
            # 容错：新版本写入的来源（如 java_rule）被旧版本读到时不抛 ValueError
            source=coerce_source(d.get("source")),
            rule_id=d.get("rule_id", ""),
            language=resolve_language_code(d.get("language"), d.get("file")),
        )


@dataclass
class TokenUsage:
    """Token 用量统计（贯穿全流程：agent 循环、压缩调用、总结调用）。"""

    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0

    def add(self, other: TokenUsage) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.calls += other.calls

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    def cost_yuan(self, input_price_per_m: float, output_price_per_m: float) -> float:
        """按单价估算成本（元）。"""
        return (
            self.input_tokens / 1_000_000 * input_price_per_m
            + self.output_tokens / 1_000_000 * output_price_per_m
        )

    def to_dict(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "calls": self.calls,
            "total": self.total,
        }


@dataclass
class ReviewReport:
    """一次完整审查的产出。"""

    target: str
    findings: list[Finding] = field(default_factory=list)
    duration_sec: float = 0.0
    usage: TokenUsage = field(default_factory=TokenUsage)
    turns: int = 0
    llm_enabled: bool = False
    stop_reason: str = ""
    compress_events: int = 0
    files_scanned: int = 0
    files_skipped: int = 0
    metadata: dict = field(default_factory=dict)

    @property
    def score(self) -> int:
        """0-100 评分：100 起步按严重级扣分。"""
        score = 100
        for f in self.findings:
            score -= f.severity.weight
        return max(0, score)

    def sorted_findings(self) -> list[Finding]:
        """严重级降序 → 文件 → 行号。"""
        order = {
            Severity.CRITICAL: 0,
            Severity.MAJOR: 1,
            Severity.MINOR: 2,
            Severity.INFO: 3,
        }
        return sorted(
            self.findings,
            key=lambda f: (order[f.severity], f.file, f.line_start),
        )

    def by_severity(self) -> dict[str, int]:
        counter: dict[str, int] = {s.value: 0 for s in Severity}
        for f in self.findings:
            counter[f.severity.value] += 1
        return counter

    def by_category(self) -> dict[str, int]:
        counter: dict[str, int] = {c.value: 0 for c in Category}
        for f in self.findings:
            counter[f.category.value] += 1
        return counter

    def to_dict(self) -> dict:
        return {
            "target": self.target,
            "score": self.score,
            "duration_sec": round(self.duration_sec, 2),
            "usage": self.usage.to_dict(),
            "turns": self.turns,
            "llm_enabled": self.llm_enabled,
            "stop_reason": self.stop_reason,
            "compress_events": self.compress_events,
            "files_scanned": self.files_scanned,
            "files_skipped": self.files_skipped,
            "by_severity": self.by_severity(),
            "by_category": self.by_category(),
            "findings": [f.to_dict() for f in self.sorted_findings()],
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ReviewReport:
        """从 to_dict() 的产物反序列化（用于加载历史报告）。"""
        usage_data = data.get("usage") or {}
        report = cls(target=str(data.get("target", "")))
        report.findings = [
            Finding.from_dict(item) for item in (data.get("findings") or [])
        ]
        report.duration_sec = float(data.get("duration_sec", 0.0) or 0.0)
        report.usage = TokenUsage(
            input_tokens=int(usage_data.get("input_tokens", 0) or 0),
            output_tokens=int(usage_data.get("output_tokens", 0) or 0),
            calls=int(usage_data.get("calls", 0) or 0),
        )
        report.turns = int(data.get("turns", 0) or 0)
        report.llm_enabled = bool(data.get("llm_enabled", False))
        report.stop_reason = str(data.get("stop_reason", "") or "")
        report.compress_events = int(data.get("compress_events", 0) or 0)
        report.files_scanned = int(data.get("files_scanned", 0) or 0)
        report.files_skipped = int(data.get("files_skipped", 0) or 0)
        report.metadata = dict(data.get("metadata") or {})
        return report


def dedup_findings(findings: list[Finding]) -> list[Finding]:
    """按 dedup_key 分组去重。

    去重规则（解决跨工具重复报告，同时不误伤同一工具的独立发现）：
    - 组内只有单一来源 → 全部保留（同行上的两条自研规则发现是不同问题，
      例如"裸 except"与"异常被吞没"）；
    - 组内有多个来源（如 Bandit 与自研规则报了同一处问题）→ 只保留
      优先级最高来源的条目。

    来源优先级：LLM（语义确认过）> 自研规则（中文描述与修复建议最完整）
    > Bandit > Ruff > radon。

    Java 规则与 Python 自研规则同级（都是 1）——它们不会同时出现在同一个
    文件的同一行上（语言互斥），排同一档只是为了让未来"跨语言同构检查"
    的出现顺序可预测。
    """
    source_priority = {
        Source.LLM: 0,
        Source.AST_RULE: 1,
        Source.JAVA_RULE: 1,
        Source.BANDIT: 2,
        Source.RUFF: 3,
        Source.RADON: 4,
    }

    groups: dict[str, list[Finding]] = {}
    for f in findings:
        groups.setdefault(f.dedup_key, []).append(f)

    result: list[Finding] = []
    for group in groups.values():
        if len(group) == 1:
            result.extend(group)
            continue
        sources = {f.source for f in group}
        if len(sources) == 1:
            result.extend(group)  # 单一来源：均为独立发现，全部保留
            continue
        best_source = min(sources, key=lambda s: source_priority.get(s, 9))
        result.extend(f for f in group if f.source == best_source)
    return result

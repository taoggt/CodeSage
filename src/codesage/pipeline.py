"""审查编排管线：一次完整审查的组装与控制。

    扫描（零 Token）
      → 确定性分析（零 Token：AST 规则 + Ruff + Bandit + radon）
      → Agent 循环（Token：复核候选 + 补充发现 + 修复建议）
      → 合并去重（LLM 驳回的候选被过滤）→ 评分 → 落盘

设计约定：
- 本模块是"组合层"：不放业务算法，只做依赖装配与流程控制；
- Harness 评测只通过 run_review() 驱动（保证评测走真实链路）；
- store 落盘失败不致命（只读目录 / 权限问题不应中断审查）。

GUI 支持（2026-11 新增，CLI 行为不变）：
- options.cancel_token：协作式取消。检查点位于每个阶段边界与逐文件循环内；
  扫描/静态阶段取消 → 直接返回空报告且不落盘；LLM 阶段取消 → 保留已有发现并落盘；
- options.persist=False：评测场景不写历史表（避免临时目录污染 reviews）；
- 新增事件 phase / progress / cancelled / persist_failed，供前端画阶段时间线。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from codesage.agent.context import build_task_brief
from codesage.agent.loop import AgentLoop, LoopConfig
from codesage.agent.policies import StopPolicy
from codesage.agent.profiles import build_system_prompt
from codesage.analyzers import default_analyzers, default_rule_engines
from codesage.core.cancel import CancelToken
from codesage.core.config import Settings, load_settings
from codesage.core.languages import (
    AUTO_MODE,
    Language,
    describe_languages,
    extensions_label,
    language_for_path,
    parse_language_mode,
)
from codesage.core.models import Finding, ReviewReport, TokenUsage, dedup_findings
from codesage.ingest.filters import FileFilter, SkipReason, default_excludes_for
from codesage.ingest.scanner import count_lines, scan
from codesage.knowledge import default_knowledge_base
from codesage.llm.budget import TokenBudget
from codesage.llm.client import LLMClient
from codesage.llm.router import ModelRouter, TaskKind
from codesage.llm.tokenizer import get_estimator
from codesage.memory.project import build_workspace_summary
from codesage.memory.store import Store
from codesage.memory.working import WorkingMemory
from codesage.tools import build_default_tools

EventHook = Callable[[str, dict[str, Any]], None]

PHASES: tuple[str, ...] = ("scan", "static", "llm", "merge", "persist")


@dataclass
class ReviewOptions:
    """一次审查的行为选项。"""

    use_llm: bool = True
    profile: str = "full"
    changed_only: bool = False
    quiet: bool = False
    max_turns: int | None = None
    on_event: EventHook | None = None
    # 语言范围："auto"（默认，按目录里实际存在的语言）或 "python" / "java" /
    # "python,java"。None 表示沿用配置值（配置默认 auto）。
    languages: str | None = None
    # ---- GUI / 评测相关（默认值保证 CLI 行为不变）
    cancel_token: CancelToken | None = None
    persist: bool = True
    persist_on_cancel: bool = True


def run_review(
    path: str | Path,
    settings: Settings | None = None,
    options: ReviewOptions | None = None,
) -> ReviewReport:
    """执行一次完整审查，返回报告。"""
    started = time.perf_counter()
    settings = settings or load_settings()
    options = options or ReviewOptions()
    token = options.cancel_token

    def is_cancelled() -> bool:
        return token is not None and token.cancelled

    target = Path(path).expanduser().resolve()
    if not target.exists():
        raise FileNotFoundError(f"路径不存在：{target}")

    workspace = target if target.is_dir() else target.parent

    def cancelled_report(phase: str) -> ReviewReport:
        """扫描/静态阶段取消：不落盘，返回空报告（调用方据 metadata 判断）。"""
        report = ReviewReport(
            target=str(target),
            findings=[],
            duration_sec=time.perf_counter() - started,
            llm_enabled=False,
            stop_reason=f"用户取消（{phase} 阶段）",
            metadata={
                "profile": options.profile,
                "cost_yuan": 0.0,
                "cancelled": True,
                "cancelled_phase": phase,
                "partial": True,
            },
        )
        _emit(options, "cancelled", {"phase": phase})
        _emit(options, "done", _done_payload(report, cancelled=True))
        return report

    if is_cancelled():
        return cancelled_report("init")

    # ---------------------------------------------------------- 1. 扫描
    _emit(options, "phase", {"name": "scan", "index": 1, "total": len(PHASES)})
    language_mode, mode_languages = parse_language_mode(
        options.languages if options.languages is not None else settings.review.languages
    )
    file_filter = FileFilter(
        workspace,
        excludes=list(settings.review.ignore_dirs),
        max_file_lines=settings.review.max_file_lines,
        languages=mode_languages,
        extra_excludes=default_excludes_for(mode_languages),
    )
    scan_result = scan(target, file_filter)
    files = scan_result.files
    # auto 模式的"探测"就发生在这里：扫描结果自带按语言的计数，
    # 不需要为了探测语言再遍历一遍目录。
    detected_languages = dict(scan_result.language_counts)

    # --changed：只保留 git 变更涉及的文件（增量审查）
    # 边界语义：非 git 仓库 → 回退全量并提示；仓库但无变更 → 明确报错
    # （绝不静默全量——用户加 --changed 时期望的是增量，静默全量会浪费 Token）
    if options.changed_only:
        from codesage.ingest.git_diff import changed_line_ranges, is_git_repo

        if not is_git_repo(workspace):
            _emit(
                options,
                "changed_fallback",
                {"reason": "当前目录不是 git 仓库，已回退为全量审查"},
            )
        else:
            ranges = changed_line_ranges(workspace)
            changed_files = {(workspace / rel).resolve() for rel in ranges}
            if not changed_files:
                raise ValueError(
                    "未检测到 git 变更（对比 HEAD）。可暂存/修改文件后重试，"
                    "或去掉 --changed 进行全量审查。"
                )
            files = [f for f in files if f.resolve() in changed_files]
            if not files:
                raise ValueError(
                    f"变更的 {len(changed_files)} 个文件中没有可审查的代码文件"
                    f"（本次语言范围：{describe_languages(mode_languages)}）。"
                )

    if not files:
        raise ValueError(
            _no_files_message(target, language_mode, mode_languages, scan_result)
        )

    _emit(options, "scan", {"files": len(files), "skipped": len(scan_result.skipped)})
    if is_cancelled():
        return cancelled_report("scan")

    # ---------------------------------------------------------- 2. 确定性分析
    _emit(options, "phase", {"name": "static", "index": 2, "total": len(PHASES)})
    det_findings: list[Finding] = []
    degraded: dict[str, str] = {}
    file_inventory: list[tuple[str, int]] = []

    # 一次读取全部源码（多处复用）
    sources: list[tuple[str, str]] = []  # (相对路径, 源码)
    for file_path in files:
        if is_cancelled():
            return cancelled_report("static")
        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = _rel_posix(file_path, workspace)
        file_inventory.append((rel, count_lines(file_path)))
        sources.append((rel, source))

    # 按语言分组：每个引擎/分析器只接触自己语言的文件。
    # 这是"混合仓库"能正确工作的关键——不做分组的话，Java 源码会被送进
    # ast.parse / ruff，产出的是噪音而不是发现。
    grouped: dict[Language, list[tuple[str, str]]] = {}
    for rel, source in sources:
        language = language_for_path(rel)
        if language is None:
            continue
        grouped.setdefault(language, []).append((rel, source))

    # 分析器按"本次实际出现的语言"筛选（而不是按配置），避免装了 Java 工具
    # 却在一个纯 Python 仓库里也去跑它。is_available 失败的不进列表——
    # 与 v1 "工具缺失就静默跳过"的语义一致。
    analyzers = [
        analyzer
        for analyzer in default_analyzers(list(grouped))
        if analyzer.is_available()
    ]

    # 自研规则引擎（零 Token，逐文件遍历）
    total_sources = max(1, len(sources))
    processed = 0
    for engine in default_rule_engines():
        if not engine.supports_any(grouped):
            continue
        if not engine.is_available():
            # 降级不静默：确定性层缺失时用户必须知道"这份报告只做了半件事"
            hint = getattr(engine, "unavailable_hint", "")
            degraded[engine.language.value] = hint or f"{engine.name} 不可用"
            continue
        for rel, source in grouped.get(engine.language, []):
            if is_cancelled():
                return cancelled_report("static")
            processed += 1
            det_findings.extend(engine.run(rel, source))
            if processed % 20 == 0 or processed == total_sources:
                _emit(
                    options,
                    "progress",
                    {"phase": "static", "done": processed, "total": total_sources},
                )

    # 外部分析器：支持批量的走单次子进程调用（大仓库性能关键路径）
    # 传入 base_dir 让分析器直接扫描真实目录（避免临时目录的写删开销）
    for analyzer in analyzers:
        for language, items in grouped.items():
            if not items or not analyzer.supports(language):
                continue
            if is_cancelled():
                return cancelled_report("static")
            if getattr(analyzer, "supports_batch", False):
                batch = analyzer.run_batch(items, base_dir=workspace)
                for findings in batch.values():
                    det_findings.extend(findings)
            else:
                for rel, source in items:
                    det_findings.extend(analyzer.run(rel, source))

    if degraded:
        _emit(options, "language_degraded", degraded)

    _emit(
        options,
        "static_done",
        {"findings": len(det_findings), "analyzers": [a.name for a in analyzers]},
    )

    # ---------------------------------------------------------- 3. Agent 循环
    _emit(options, "phase", {"name": "llm", "index": 3, "total": len(PHASES)})
    llm_findings: list[Finding] = []
    dismissed: list[dict] = []
    loop_usage = TokenUsage()
    turns = 0
    stop_reason = ""
    compress_events = 0
    llm_used = False
    cancelled_reason: str | None = None
    llm_failures = 0
    llm_degraded = False

    # 轮次上限的唯一真相源：任务简报、StopPolicy、诊断都读同一个值。
    # 旧版简报硬编码 settings.review.max_turns，--max-turns 3 时模型仍被
    # 告知「最多 12 轮」，而 LoopConfig.max_turns 又是个从未被读取的死字段。
    effective_turns = options.max_turns or settings.review.max_turns

    # 已读文件台账（read_file 写入）：压缩钉住块与覆盖率统计的数据源
    visit_log: list[str] = []
    context_peak: dict[str, float] = {"tokens": 0.0, "ratio": 0.0}

    store = _open_store(settings.review.store_path)
    try:
        if is_cancelled():
            cancelled_reason = "llm"
        elif options.use_llm and settings.llm_configured:
            llm_used = True
            client = LLMClient(settings.llm)
            router = ModelRouter(settings.llm)
            estimator = get_estimator()
            budget = TokenBudget(settings.budget)
            memory = WorkingMemory(active_window_turns=settings.review.active_window_turns)

            knowledge_base = default_knowledge_base()
            registry = build_default_tools(
                workspace=workspace,
                analyzers=analyzers,
                knowledge_base=knowledge_base,
                findings_collector=llm_findings,
                dismissed_collector=dismissed,
                read_limit=settings.review.tool_output_limit,
                visit_log=visit_log,
                candidates=det_findings,
            )

            def pinned_state() -> str:
                """压缩钉住块：只放能从确定性状态重建的事实，不交给 LLM。

                已提交发现在 llm_findings 里、已读文件在 visit_log 里，
                每次压缩都用最新状态覆盖，摘要弄丢它们已无从谈起。
                """
                lines = [f"### 已提交发现（共 {len(llm_findings)} 条）"]
                if llm_findings:
                    for idx, f in enumerate(llm_findings, 1):
                        lines.append(
                            f"{idx}. {f.file}:{f.line_start}-{f.line_end} "
                            f"[{f.category.value}/{f.severity.value}] {f.title}"
                        )
                else:
                    lines.append("（暂无）")
                read = sorted(set(visit_log))
                lines.append(f"### 已读文件（共 {len(read)} 个）")
                lines.extend(f"- {p}" for p in read[:200])
                return "\n".join(lines)

            def loop_event(event: str, payload: dict[str, Any]) -> None:
                """转发事件的同时记录上下文峰值，供预算可达性诊断。"""
                if event == "context":
                    context_peak["tokens"] = max(
                        context_peak["tokens"], float(payload.get("tokens", 0))
                    )
                    context_peak["ratio"] = max(
                        context_peak["ratio"], float(payload.get("ratio", 0.0))
                    )
                _emit(options, event, payload)

            lessons_block = ""
            if store is not None:
                try:
                    from codesage.optim.lessons import LessonBook

                    lessons_block = LessonBook(store).few_shot_block(
                        project=str(workspace)
                    )
                except Exception:  # noqa: BLE001 - 错题本读取失败不影响审查
                    lessons_block = ""

            system_prompt = build_system_prompt(
                options.profile,
                workspace_summary=build_workspace_summary(workspace, files),
                lessons_block=lessons_block,
                budget_hint=(
                    f"最多可执行 {effective_turns} 个回合"
                    "（每回合可发起多个工具调用），请合理分配。"
                ),
                languages=list(grouped),
            )
            brief = build_task_brief(
                target=str(target),
                files=file_inventory,
                candidates=det_findings,
                max_candidates=settings.review.max_candidates_in_prompt,
                changed_only=options.changed_only,
                languages=detected_languages,
            )

            compressor = _make_compressor(client, router, budget)
            loop = AgentLoop(
                client=client,
                registry=registry,
                memory=memory,
                budget=budget,
                estimator=estimator,
                model=router.model_for(TaskKind.REVIEW),
                policy=StopPolicy(max_turns=effective_turns),
                config=LoopConfig(),
                compressor=compressor,
                pinned_state=pinned_state,
                findings_counter=lambda: len(llm_findings),
                on_event=loop_event,
                cancel_token=token,
            )

            result = loop.run(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": brief},
                ]
            )
            loop_usage = result.usage
            turns = result.turns
            stop_reason = result.stop_reason
            compress_events = result.compress_events
            llm_failures = result.llm_failures
            llm_degraded = result.status == "degraded"
            if result.status == "cancelled":
                cancelled_reason = "llm"

        elif options.use_llm:
            stop_reason = "未配置 API Key，已降级为确定性分析"

        # ------------------------------------------------------ 4. 合并与聚合
        _emit(options, "phase", {"name": "merge", "index": 4, "total": len(PHASES)})
        merged = _merge_findings(det_findings, llm_findings, dismissed)
        duration = time.perf_counter() - started

        # 预算可达性诊断：让「压缩没触发」从沉默变成一条可观测数据。
        # 默认 12 轮 + 6000 字符工具结果上限下上下文峰值通常只有 soft 的
        # 两三成，压缩/熔断在默认配置里根本不会触发；不写进报告，用户和
        # 面试追问都无从判断这套机制到底跑没跑过。
        soft_tokens = int(
            settings.budget.effective_budget_window * settings.budget.soft_ratio
        )
        diagnostics: dict[str, Any] = {
            "peak_context_tokens": int(context_peak["tokens"]),
            "peak_ratio": round(context_peak["ratio"], 3),
            "soft_threshold_tokens": soft_tokens,
            "compress_events": compress_events,
            "compression_triggered": compress_events > 0,
            "max_turns": effective_turns,
        }
        if llm_used and compress_events == 0:
            diagnostics["note"] = (
                f"本次上下文峰值 {int(context_peak['tokens'])} token，"
                f"未达到 soft 阈值 {soft_tokens} token，压缩未触发。"
                "如需验证压缩链路：调大 review.max_turns（≥24）"
                "并调小 budget.budget_window（≈60000）。"
            )

        metadata: dict[str, Any] = {
            "profile": options.profile,
            "cost_yuan": 0.0,
            "candidates": len(det_findings),
            "llm_findings": len(llm_findings),
            "dismissed": dismissed,
            "analyzers": [a.name for a in analyzers],
            # ---- 循环可观测性（审计后新增）
            "max_turns": effective_turns,
            "llm_failures": llm_failures,
            "llm_degraded": llm_degraded,
            "files_read": sorted(set(visit_log)),
            "coverage_ratio": (
                round(len(set(visit_log)) / len(file_inventory), 3)
                if file_inventory
                else 0.0
            ),
            "budget_diagnostics": diagnostics,
            # ---- 多语言（只增字段，老 GUI 忽略即可）
            "language_mode": language_mode,
            "languages": detected_languages,
            "language_degraded": degraded,
            "scan_skip_counts": dict(scan_result.skip_counts),
            "scan_skipped": [
                {"file": f, "reason": r} for f, r in scan_result.skipped[:20]
            ],
            # 本次实际扫描过的文件（供 GUI 画"哪些文件已审过"的覆盖图，截断到 500）
            "files": [rel for rel, _ in file_inventory][:500],
        }
        if cancelled_reason is not None:
            metadata.update(
                {
                    "cancelled": True,
                    "cancelled_phase": cancelled_reason,
                    "partial": True,
                }
            )

        report = ReviewReport(
            target=str(target),
            findings=merged,
            duration_sec=duration,
            usage=loop_usage,
            turns=turns,
            llm_enabled=llm_used,
            stop_reason=stop_reason or ("用户取消" if cancelled_reason else ""),
            compress_events=compress_events,
            files_scanned=len(files),
            files_skipped=len(scan_result.skipped),
            metadata=metadata,
        )

        # 成本估算（若有 LLM 调用）
        if llm_used:
            report.metadata["cost_yuan"] = round(
                loop_usage.cost_yuan(
                    settings.budget.input_price_per_m,
                    settings.budget.output_price_per_m,
                ),
                4,
            )

        # ------------------------------------------------------ 5. 落盘
        should_persist = (
            options.persist
            and store is not None
            and (cancelled_reason is None or options.persist_on_cancel)
        )
        if should_persist:
            _emit(options, "phase", {"name": "persist", "index": 5, "total": len(PHASES)})
            try:
                review_id = store.save_review(report, project=str(workspace))
                report.metadata["review_id"] = review_id
            except Exception as exc:  # noqa: BLE001 - 落盘失败不致命
                _emit(options, "persist_failed", {"error": str(exc)})
    finally:
        if store is not None:
            store.close()

    if cancelled_reason is not None:
        _emit(options, "cancelled", {"phase": cancelled_reason})

    duration = time.perf_counter() - started
    report.duration_sec = duration
    _emit(options, "done", _done_payload(report, cancelled=cancelled_reason is not None))
    return report


# ---------------------------------------------------------------- 内部工具


def _no_files_message(
    target: Path,
    language_mode: str,
    languages: tuple[Language, ...],
    scan_result,
) -> str:
    """空文件集的报错文案（三态，避免"只支持 Python"的旧措辞误导）。"""
    supported = extensions_label(languages) or "受支持后缀"
    quiet = scan_result.skip_counts.get(SkipReason.UNSUPPORTED_LANGUAGE.value, 0)
    tail = f"跳过 {len(scan_result.skipped)} 个候选"
    if quiet:
        tail += f"，另有 {quiet} 个非目标语言文件"

    if language_mode == AUTO_MODE:
        return f"没有可审查的文件（{target}）：未发现 {supported} 文件（{tail}）。"
    return (
        f"没有可审查的 {describe_languages(languages)} 文件（{target}）："
        f"已限定语言为 {language_mode}，但目录中没有 {supported} 文件（{tail}）。"
    )


def _done_payload(report: ReviewReport, cancelled: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "findings": len(report.findings),
        "score": report.score,
        "duration": round(report.duration_sec, 2),
    }
    review_id = report.metadata.get("review_id")
    if review_id:
        payload["review_id"] = review_id
    if cancelled:
        payload["cancelled"] = True
        payload["partial"] = True
    return payload


def _merge_findings(
    det_findings: list[Finding],
    llm_findings: list[Finding],
    dismissed: list[dict],
) -> list[Finding]:
    """合并确定性发现与 LLM 发现，并应用驳回过滤。

    驳回过滤：候选发现（来自规则/外部工具）若被 LLM 复核为误报，
    从最终报告中移除（容差 ±2 行）。
    """
    if dismissed:

        def is_dismissed(finding: Finding) -> bool:
            if finding.source.value == "llm":
                return False
            for d in dismissed:
                if d["file"] != finding.file:
                    continue
                if abs(d["line"] - finding.line_start) <= 2:
                    return True
            return False

        det_findings = [f for f in det_findings if not is_dismissed(f)]

    return dedup_findings([*det_findings, *llm_findings])


def _make_compressor(client: LLMClient, router: ModelRouter, budget: TokenBudget):
    """构造记忆压缩器（用快模型，成本优先）。

    注入给 WorkingMemory：输入压缩 prompt，输出结构化摘要。
    """

    def compressor(prompt: str) -> str:
        response = client.chat(
            [{"role": "user", "content": prompt}],
            model=router.model_for(TaskKind.COMPRESS),
            temperature=0.0,
        )
        budget.record(response.usage)
        return response.content

    return compressor


def _open_store(store_path: str = "") -> Store | None:
    """打开全局存储（失败返回 None，不影响审查主流程）。

    默认位置：~/.codesage/store.db（用户级，不污染被审查项目）；
    可通过配置 review.store_path 覆盖（测试用）。
    """
    try:
        from codesage.memory.store import default_store_path

        path = Path(store_path) if store_path else default_store_path()
        return Store(path)
    except Exception:  # noqa: BLE001
        return None


def _rel_posix(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def _emit(options: ReviewOptions, event: str, payload: dict[str, Any]) -> None:
    if options.on_event is not None:
        try:
            options.on_event(event, payload)
        except Exception:  # noqa: BLE001
            pass

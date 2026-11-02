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
from codesage.analyzers import RuleEngine, default_analyzers
from codesage.core.cancel import CancelToken
from codesage.core.config import Settings, load_settings
from codesage.core.models import Finding, ReviewReport, TokenUsage, dedup_findings
from codesage.ingest.filters import FileFilter
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
    file_filter = FileFilter(
        workspace,
        excludes=list(settings.review.ignore_dirs),
        max_file_lines=settings.review.max_file_lines,
    )
    scan_result = scan(target, file_filter)
    files = scan_result.files

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
                    f"变更的 {len(changed_files)} 个文件中没有可审查的 Python 文件。"
                )

    if not files:
        raise ValueError(
            f"没有可审查的 Python 文件（{target}）。"
            f"跳过 {len(scan_result.skipped)} 个候选。"
        )

    _emit(options, "scan", {"files": len(files), "skipped": len(scan_result.skipped)})
    if is_cancelled():
        return cancelled_report("scan")

    # ---------------------------------------------------------- 2. 确定性分析
    _emit(options, "phase", {"name": "static", "index": 2, "total": len(PHASES)})
    det_findings: list[Finding] = []
    engine = RuleEngine()
    analyzers = [a for a in default_analyzers() if a.is_available()]
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

    # 自研规则引擎（零 Token，逐文件遍历）
    total_sources = max(1, len(sources))
    for index, (rel, source) in enumerate(sources, start=1):
        if is_cancelled():
            return cancelled_report("static")
        det_findings.extend(engine.run(rel, source))
        if index % 20 == 0 or index == total_sources:
            _emit(
                options,
                "progress",
                {"phase": "static", "done": index, "total": total_sources},
            )

    # 外部分析器：支持批量的走单次子进程调用（大仓库性能关键路径）
    # 传入 base_dir 让分析器直接扫描真实目录（避免临时目录的写删开销）
    for analyzer in analyzers:
        if is_cancelled():
            return cancelled_report("static")
        if getattr(analyzer, "supports_batch", False):
            batch = analyzer.run_batch(sources, base_dir=workspace)
            for findings in batch.values():
                det_findings.extend(findings)
        else:
            for rel, source in sources:
                det_findings.extend(analyzer.run(rel, source))

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
            )

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
                    f"最多可执行 {settings.review.max_turns} 轮工具调用，请合理分配。"
                ),
            )
            brief = build_task_brief(
                target=str(target),
                files=file_inventory,
                candidates=det_findings,
                max_candidates=settings.review.max_candidates_in_prompt,
                changed_only=options.changed_only,
            )

            compressor = _make_compressor(client, router, budget)
            loop = AgentLoop(
                client=client,
                registry=registry,
                memory=memory,
                budget=budget,
                estimator=estimator,
                model=router.model_for(TaskKind.REVIEW),
                policy=StopPolicy(
                    max_turns=options.max_turns or settings.review.max_turns
                ),
                config=LoopConfig(
                    max_turns=options.max_turns or settings.review.max_turns,
                ),
                compressor=compressor,
                findings_counter=lambda: len(llm_findings),
                on_event=options.on_event,
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
            if result.status == "cancelled":
                cancelled_reason = "llm"

        elif options.use_llm:
            stop_reason = "未配置 API Key，已降级为确定性分析"

        # ------------------------------------------------------ 4. 合并与聚合
        _emit(options, "phase", {"name": "merge", "index": 4, "total": len(PHASES)})
        merged = _merge_findings(det_findings, llm_findings, dismissed)
        duration = time.perf_counter() - started

        metadata: dict[str, Any] = {
            "profile": options.profile,
            "cost_yuan": 0.0,
            "candidates": len(det_findings),
            "llm_findings": len(llm_findings),
            "dismissed": dismissed,
            "analyzers": [a.name for a in analyzers],
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

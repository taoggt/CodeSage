import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import clsx from "clsx";

import { api, downloadFile, qs } from "../api/client";
import { useReview, useSubmitFeedback } from "../api/queries";
import type { FileContent, Finding } from "../api/types";
import { ScoreRing } from "../components/Badges";
import { EmptyState } from "../components/EmptyState";
import { FindingCard, FindingList } from "../components/Findings";
import { formatCost, formatDuration, formatNumber } from "../lib/format";
import { CATEGORY_ZH, SOURCE_ZH, severityMeta } from "../lib/severity";

export function Report() {
  const params = useParams();
  const reviewId = Number(params.id);
  const review = useReview(Number.isFinite(reviewId) ? reviewId : null);
  const feedback = useSubmitFeedback();

  const [severity, setSeverity] = useState<string>("all");
  const [category, setCategory] = useState<string>("all");
  const [source, setSource] = useState<string>("all");
  const [keyword, setKeyword] = useState("");
  const [activeId, setActiveId] = useState<string | null>(null);
  const [toast, setToast] = useState("");
  const [showMeta, setShowMeta] = useState(false);

  const data = review.data;
  const findings = data?.findings ?? [];
  const project = data?.review?.project || "";

  const filtered = useMemo(() => {
    const text = keyword.trim().toLowerCase();
    return findings.filter((finding) => {
      if (severity !== "all" && finding.severity !== severity) return false;
      if (category !== "all" && finding.category !== category) return false;
      if (source !== "all" && finding.source !== source) return false;
      if (!text) return true;
      return (
        finding.title.toLowerCase().includes(text) ||
        finding.file.toLowerCase().includes(text) ||
        (finding.description || "").toLowerCase().includes(text)
      );
    });
  }, [findings, severity, category, source, keyword]);

  const active =
    findings.find((finding) => finding.id === activeId) || filtered[0] || null;

  const context = useQuery({
    queryKey: ["context", project, active?.file, active?.line_start],
    queryFn: () =>
      api<FileContent>(
        `/workspace/file${qs({
          root: project,
          path: active!.file,
          start: Math.max(1, active!.line_start - 10),
          end: active!.line_end + 14,
        })}`,
      ),
    enabled: !!project && !!active?.file,
    retry: 0,
  });

  if (review.isLoading) {
    return <EmptyState title="加载报告…" />;
  }
  if (review.error || !data) {
    return (
      <EmptyState
        title="找不到这份报告"
        hint={(review.error as Error)?.message || "记录可能已被删除"}
        action={
          <Link to="/history" className="mt-2 text-[11.5px] text-blue-600 hover:underline">
            返回历史记录
          </Link>
        }
      />
    );
  }

  const bySeverity = data.by_severity || {};
  const feedbackMap: Record<string, { verdict: string; note: string }[]> = {};
  for (const [findingId, items] of Object.entries(data.feedback_by_finding || {})) {
    feedbackMap[findingId] = items.map((item) => ({ verdict: item.verdict, note: item.note }));
  }

  async function submit(verdict: "wrong" | "right") {
    if (!active) return;
    setToast("");
    try {
      const result = await feedback.mutateAsync({
        finding_id: active.id,
        verdict,
        review_id: reviewId,
      });
      setToast(
        result.lesson_created
          ? `已记录并生成错题本经验 #${result.lesson_id}，下次审查会注入 Prompt`
          : verdict === "right"
            ? "已确认这条发现（计入统计，不生成经验）"
            : "已记录反馈",
      );
    } catch (error) {
      setToast((error as Error).message);
    }
  }

  return (
    <div className="flex h-full flex-col">
      {/* KPI 头 */}
      <div className="shrink-0 border-b border-ink-200 bg-white px-5 py-3">
        <div className="flex flex-wrap items-center gap-4">
          <div className="flex items-center gap-3">
            <ScoreRing score={data.score} size={54} />
            <div>
              <div className="text-[10.5px] uppercase tracking-wide text-ink-400">综合评分</div>
              <div className="font-mono text-[11px] text-ink-500">{data.target}</div>
            </div>
          </div>

          <div className="flex items-center gap-2">
            {(["critical", "major", "minor", "info"] as const).map((key) => {
              const meta = severityMeta(key);
              return (
                <div
                  key={key}
                  className={clsx("rounded border px-2 py-1", meta.bg, meta.border)}
                  title={`${meta.label} ${meta.zh}`}
                >
                  <div className={clsx("text-[9.5px] font-medium", meta.text)}>{meta.label}</div>
                  <div className={clsx("text-[14px] font-semibold leading-none", meta.text)}>
                    {bySeverity[key] || 0}
                  </div>
                </div>
              );
            })}
          </div>

          <div className="text-[10.5px] leading-relaxed text-ink-500">
            <div>{data.findings.length} 条发现 · {data.files_scanned} 个文件</div>
            <div>
              {formatDuration(data.duration_sec)} · {formatNumber(data.usage?.total)} tokens ·{" "}
              {formatCost(data.metadata?.cost_yuan)}
            </div>
            <div>
              {data.llm_enabled ? `LLM 审查 · ${data.turns} 轮` : "确定性分析（未启用 LLM）"}
              {data.compress_events ? ` · 压缩 ${data.compress_events} 次` : ""}
            </div>
          </div>

          <div className="ml-auto flex items-center gap-2">
            <button
              type="button"
              onClick={() => setShowMeta((value) => !value)}
              className="rounded border border-ink-200 px-2 py-1 text-[11px] text-ink-600 hover:bg-ink-50"
            >
              审查元信息
            </button>
            <button
              type="button"
              onClick={() => downloadFile(`/reviews/${reviewId}/export?format=md`, `report-${reviewId}.md`)}
              className="rounded border border-ink-200 px-2 py-1 text-[11px] text-ink-600 hover:bg-ink-50"
            >
              导出 Markdown
            </button>
            <button
              type="button"
              onClick={() => downloadFile(`/reviews/${reviewId}/export?format=json`, `report-${reviewId}.json`)}
              className="rounded border border-ink-200 px-2 py-1 text-[11px] text-ink-600 hover:bg-ink-50"
            >
              JSON
            </button>
          </div>
        </div>

        {showMeta && (
          <div className="mt-3 grid gap-x-6 gap-y-1 rounded border border-ink-100 bg-ink-50/60 p-2.5 text-[10.5px] text-ink-600 sm:grid-cols-2 lg:grid-cols-4">
            <span>记录 #{reviewId} · {data.review?.created_at}</span>
            <span>结束原因：{data.stop_reason || "模型主动结束"}</span>
            <span>候选 {data.metadata?.candidates ?? 0} 条 · LLM 补充 {data.metadata?.llm_findings ?? 0} 条</span>
            <span>驳回 {data.metadata?.dismissed?.length ?? 0} 条被复核为误报</span>
            <span>跳过 {data.files_skipped} 个文件</span>
            <span>分析器：{(data.metadata?.analyzers || []).join(" / ") || "—"}</span>
            <span>部分结果：{data.review?.partial ? "是（中途取消）" : "否"}</span>
            <span>项目：{project || "—"}</span>
          </div>
        )}

        {toast && (
          <div className="mt-2 rounded bg-ink-900 px-2.5 py-1.5 text-[11px] text-white">{toast}</div>
        )}
      </div>

      {/* 主体：左列表 + 右详情 */}
      <div className="flex min-h-0 flex-1">
        <div className="flex w-[360px] shrink-0 flex-col border-r border-ink-200 bg-white">
          <div className="space-y-2 border-b border-ink-100 px-3 py-2.5">
            <div className="flex items-center gap-1">
              <FilterChip active={severity === "all"} onClick={() => setSeverity("all")}>
                全部 {findings.length}
              </FilterChip>
              {(["critical", "major", "minor", "info"] as const).map((key) => (
                <FilterChip
                  key={key}
                  active={severity === key}
                  onClick={() => setSeverity(key)}
                >
                  {severityMeta(key).label} {bySeverity[key] || 0}
                </FilterChip>
              ))}
            </div>
            <div className="flex gap-1.5">
              <select
                value={category}
                onChange={(event) => setCategory(event.target.value)}
                className="min-w-0 flex-1 rounded border border-ink-200 bg-white px-1.5 py-1 text-[10.5px] text-ink-600"
              >
                <option value="all">全部类别</option>
                {Object.entries(CATEGORY_ZH).map(([key, label]) => (
                  <option key={key} value={key}>
                    {label}
                  </option>
                ))}
              </select>
              <select
                value={source}
                onChange={(event) => setSource(event.target.value)}
                className="min-w-0 flex-1 rounded border border-ink-200 bg-white px-1.5 py-1 text-[10.5px] text-ink-600"
              >
                <option value="all">全部来源</option>
                {Object.entries(SOURCE_ZH).map(([key, label]) => (
                  <option key={key} value={key}>
                    {label}
                  </option>
                ))}
              </select>
            </div>
            <input
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
              placeholder="搜索标题 / 文件"
              className="w-full rounded border border-ink-200 px-2 py-1 text-[11px] outline-none focus:border-blue-400"
            />
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto">
            {filtered.length === 0 ? (
              <div className="p-6 text-center text-[11.5px] text-ink-400">
                没有匹配的发现
              </div>
            ) : (
              <FindingList
                findings={filtered}
                activeId={active?.id ?? null}
                onSelect={(finding: Finding) => setActiveId(finding.id)}
                feedback={feedbackMap}
              />
            )}
          </div>
        </div>

        <div className="min-w-0 flex-1 bg-white">
          {active ? (
            <FindingCard
              finding={active}
              context={context.data?.content}
              pending={feedback.isPending}
              onFeedback={submit}
            />
          ) : (
            <EmptyState title="没有可展示的发现" hint="这份报告没有发现问题，或筛选条件过窄。" />
          )}
        </div>
      </div>
    </div>
  );
}

function FilterChip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={clsx(
        "rounded border px-1.5 py-0.5 text-[10.5px]",
        active
          ? "border-ink-900 bg-ink-900 text-white"
          : "border-ink-200 text-ink-600 hover:bg-ink-50",
      )}
    >
      {children}
    </button>
  );
}

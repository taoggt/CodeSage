import clsx from "clsx";

import type { Finding } from "../api/types";
import { CATEGORY_ZH, SOURCE_ZH, severityMeta } from "../lib/severity";
import { SeverityBadge } from "./Badges";

interface ListProps {
  findings: Finding[];
  activeId: string | null;
  onSelect: (finding: Finding) => void;
  feedback?: Record<string, { verdict: string; note: string }[]>;
}

export function FindingList({ findings, activeId, onSelect, feedback }: ListProps) {
  if (findings.length === 0) {
    return (
      <div className="px-3 py-6 text-center text-xs text-ink-400">
        当前筛选条件下没有问题
      </div>
    );
  }
  return (
    <div className="divide-y divide-ink-100">
      {findings.map((finding) => {
        const marked = feedback?.[finding.id];
        return (
          <button
            key={finding.id}
            type="button"
            onClick={() => onSelect(finding)}
            className={clsx(
              "w-full px-3 py-2.5 text-left transition-colors",
              activeId === finding.id ? "bg-blue-50" : "hover:bg-ink-50",
              marked && "opacity-70",
            )}
          >
            <div className="flex items-center gap-1.5">
              <SeverityBadge severity={finding.severity} compact />
              <span className="truncate text-[12.5px] font-medium text-ink-800">
                {finding.title}
              </span>
              {marked && (
                <span className="ml-auto shrink-0 rounded bg-ink-100 px-1 text-[9.5px] text-ink-500">
                  {marked[0].verdict === "wrong" ? "已标误报" : marked[0].verdict === "right" ? "已确认" : "已标漏报"}
                </span>
              )}
            </div>
            <div className="mt-1 flex items-center gap-1.5 text-[10.5px] text-ink-400">
              <span className="truncate">{finding.file}</span>
              <span>:{finding.line_start}</span>
              <span className="ml-auto shrink-0">{CATEGORY_ZH[finding.category] || finding.category}</span>
            </div>
          </button>
        );
      })}
    </div>
  );
}

interface CardProps {
  finding: Finding;
  context?: string;
  onFeedback?: (verdict: "wrong" | "right") => void;
  onOpenFile?: () => void;
  pending?: boolean;
}

export function FindingCard({ finding, context, onFeedback, onOpenFile, pending }: CardProps) {
  const meta = severityMeta(finding.severity);
  const location =
    finding.line_end && finding.line_end !== finding.line_start
      ? `${finding.file}:${finding.line_start}-${finding.line_end}`
      : `${finding.file}:${finding.line_start}`;

  return (
    <div className="flex h-full flex-col">
      <div className={clsx("border-b px-4 py-3", meta.bg, meta.border)}>
        <div className="flex items-center gap-2">
          <SeverityBadge severity={finding.severity} />
          <span className={clsx("text-[11px]", meta.text)}>{meta.zh}</span>
          <span className="ml-auto text-[10.5px] text-ink-500">
            {CATEGORY_ZH[finding.category] || finding.category}
          </span>
        </div>
        <h3 className="mt-1.5 text-[14px] font-semibold leading-snug text-ink-900">
          {finding.title}
        </h3>
        {onOpenFile ? (
          <button
            type="button"
            onClick={onOpenFile}
            className="mt-1 font-mono text-[10.5px] text-ink-500 underline-offset-2 hover:underline"
            title="跳转到代码位置"
          >
            {location}
          </button>
        ) : (
          <span className="mt-1 block font-mono text-[10.5px] text-ink-500">{location}</span>
        )}
      </div>

      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-3">
        {finding.description && (
          <section>
            <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-ink-400">
              描述
            </div>
            <p className="whitespace-pre-wrap text-[12.5px] leading-relaxed text-ink-700">
              {finding.description}
            </p>
          </section>
        )}

        {(finding.evidence || context) && (
          <section>
            <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-ink-400">
              {finding.evidence ? "证据" : `上下文（第 ${finding.line_start} 行附近）`}
            </div>
            <pre className="overflow-x-auto rounded border border-ink-200 bg-ink-50 p-2 font-mono text-[11px] leading-relaxed text-ink-700">
              {finding.evidence || context}
            </pre>
          </section>
        )}

        {finding.suggestion && (
          <section>
            <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-ink-400">
              建议
            </div>
            <p className="whitespace-pre-wrap rounded border border-emerald-100 bg-emerald-50/60 px-2.5 py-2 text-[12.5px] leading-relaxed text-emerald-900">
              {finding.suggestion}
            </p>
          </section>
        )}

        <section className="flex flex-wrap items-center gap-2 border-t border-ink-100 pt-2 text-[10.5px] text-ink-500">
          <span className="rounded bg-ink-100 px-1.5 py-0.5">
            {SOURCE_ZH[finding.source] || finding.source}
          </span>
          {finding.rule_id && <span className="font-mono">{finding.rule_id}</span>}
          <span className="ml-auto">置信度 {Math.round((finding.confidence || 0) * 100)}%</span>
        </section>
      </div>

      {onFeedback && (
        <div className="flex shrink-0 items-center gap-2 border-t border-ink-200 bg-white px-4 py-2.5">
          <button
            type="button"
            disabled={pending}
            onClick={() => onFeedback("wrong")}
            className="rounded-md border border-ink-200 px-2.5 py-1 text-[11.5px] text-ink-600 hover:border-red-300 hover:bg-red-50 hover:text-red-700 disabled:opacity-50"
            title="标记为误报 → 自动生成错题本经验"
          >
            误报
          </button>
          <button
            type="button"
            disabled={pending}
            onClick={() => onFeedback("right")}
            className="rounded-md border border-ink-200 px-2.5 py-1 text-[11.5px] text-ink-600 hover:border-emerald-300 hover:bg-emerald-50 hover:text-emerald-700 disabled:opacity-50"
          >
            确认
          </button>
          <span className="ml-auto text-[10px] text-ink-400">
            误报会立即写入错题本，下次审查注入 Prompt
          </span>
        </div>
      )}
    </div>
  );
}

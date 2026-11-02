import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import clsx from "clsx";

import { useCompare, useReviews } from "../api/queries";
import { EmptyState } from "../components/EmptyState";
import { formatCost, formatDuration, formatNumber, relativeTime } from "../lib/format";
import { severityMeta } from "../lib/severity";

const PAGE_SIZE = 20;

export function History() {
  const [offset, setOffset] = useState(0);
  const [project, setProject] = useState("");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<number[]>([]);

  const reviews = useReviews({
    limit: PAGE_SIZE,
    offset,
    project: project || undefined,
    q: query || undefined,
  });
  const compare = useCompare(selected[0] ?? null, selected[1] ?? null);

  const items = reviews.data?.items ?? [];
  const total = reviews.data?.total ?? 0;
  const trend = useMemo(
    () =>
      [...items]
        .sort((a, b) => a.id - b.id)
        .map((item) => ({
          id: item.id,
          score: item.score,
          findings: item.findings_count,
          label: item.created_at?.slice(5, 16) ?? "",
        })),
    [items],
  );

  function toggle(id: number) {
    setSelected((current) => {
      if (current.includes(id)) return current.filter((item) => item !== id);
      if (current.length >= 2) return [current[1], id];
      return [...current, id];
    });
  }

  return (
    <div className="flex h-full flex-col">
      <div className="shrink-0 border-b border-ink-200 bg-white px-5 py-3">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-[13px] font-semibold">审查历史</h1>
          <span className="text-[10.5px] text-ink-400">共 {total} 条</span>
          <div className="ml-auto flex items-center gap-2">
            <input
              value={project}
              onChange={(event) => {
                setProject(event.target.value);
                setOffset(0);
              }}
              placeholder="按项目路径过滤（如 D:/proj）"
              className="w-[260px] rounded border border-ink-200 px-2 py-1 font-mono text-[11px] outline-none focus:border-blue-400"
            />
            <input
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                setOffset(0);
              }}
              placeholder="搜索目标路径"
              className="w-[160px] rounded border border-ink-200 px-2 py-1 text-[11px] outline-none focus:border-blue-400"
            />
          </div>
        </div>

        {trend.length > 1 && (
          <div className="mt-3 h-[120px] w-full">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={trend} margin={{ top: 4, right: 8, left: -18, bottom: 0 }}>
                <CartesianGrid stroke="#e2e8f0" strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="label" tick={{ fontSize: 9, fill: "#94a3b8" }} tickLine={false} />
                <YAxis domain={[0, 100]} tick={{ fontSize: 9, fill: "#94a3b8" }} axisLine={false} tickLine={false} />
                <Tooltip
                  contentStyle={{ fontSize: 11, borderRadius: 6, borderColor: "#e2e8f0" }}
                  labelFormatter={(value) => String(value)}
                  formatter={(value: number, name) => [value, name === "score" ? "评分" : "发现数"]}
                />
                <Line type="monotone" dataKey="score" stroke="#0f172a" strokeWidth={1.6} dot={{ r: 2 }} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>

      <div className="flex min-h-0 flex-1">
        <div className="min-w-0 flex-1 overflow-auto bg-white">
          {reviews.isLoading ? (
            <div className="p-6 text-[12px] text-ink-400">加载中…</div>
          ) : items.length === 0 ? (
            <EmptyState
              title="还没有审查记录"
              hint="回到工作台选择目录并运行一次审查，记录会自动写入这里（~/.codesage/store.db）。"
              action={
                <Link to="/" className="mt-2 text-[11.5px] text-blue-600 hover:underline">
                  去工作台 →
                </Link>
              }
            />
          ) : (
            <table className="w-full border-collapse text-[11.5px]">
              <thead className="sticky top-0 z-10 bg-ink-50 text-[10.5px] text-ink-500">
                <tr>
                  <th className="w-8 px-2 py-1.5" />
                  <th className="px-2 py-1.5 text-left font-medium">时间</th>
                  <th className="px-2 py-1.5 text-left font-medium">目标</th>
                  <th className="px-2 py-1.5 text-right font-medium">评分</th>
                  <th className="px-2 py-1.5 text-left font-medium">问题分布</th>
                  <th className="px-2 py-1.5 text-right font-medium">耗时</th>
                  <th className="px-2 py-1.5 text-right font-medium">Token</th>
                  <th className="px-2 py-1.5 text-right font-medium">成本</th>
                  <th className="px-2 py-1.5 text-center font-medium">模式</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr
                    key={item.id}
                    className={clsx(
                      "border-b border-ink-100 hover:bg-ink-50",
                      selected.includes(item.id) && "bg-blue-50/60",
                    )}
                  >
                    <td className="px-2 py-1.5 text-center">
                      <input
                        type="checkbox"
                        checked={selected.includes(item.id)}
                        onChange={() => toggle(item.id)}
                        className="h-3 w-3 accent-ink-900"
                      />
                    </td>
                    <td className="whitespace-nowrap px-2 py-1.5 text-ink-500" title={item.created_at}>
                      {relativeTime(item.created_at)}
                    </td>
                    <td className="max-w-[280px] truncate px-2 py-1.5">
                      <Link to={`/report/${item.id}`} className="hover:text-blue-600 hover:underline">
                        {item.target_display}
                      </Link>
                      {item.partial && (
                        <span className="ml-1 rounded bg-amber-50 px-1 text-[9.5px] text-amber-700">
                          部分
                        </span>
                      )}
                    </td>
                    <td className="px-2 py-1.5 text-right">
                      <span
                        className={clsx(
                          "rounded px-1.5 py-0.5 font-medium",
                          item.score >= 90
                            ? "bg-emerald-50 text-emerald-700"
                            : item.score >= 75
                              ? "bg-amber-50 text-amber-700"
                              : "bg-red-50 text-red-700",
                        )}
                      >
                        {item.score}
                      </span>
                    </td>
                    <td className="px-2 py-1.5">
                      <div className="flex items-center gap-1.5">
                        {(["critical", "major", "minor", "info"] as const).map((key) => {
                          const count = item.by_severity?.[key] || 0;
                          if (!count) return null;
                          return (
                            <span key={key} className="inline-flex items-center gap-0.5 text-[10px]">
                              <span className={clsx("h-1.5 w-1.5 rounded-full", severityMeta(key).dot)} />
                              {count}
                            </span>
                          );
                        })}
                        {!item.findings_count && <span className="text-[10px] text-ink-400">无</span>}
                      </div>
                    </td>
                    <td className="whitespace-nowrap px-2 py-1.5 text-right text-ink-500">
                      {formatDuration(item.duration_sec)}
                    </td>
                    <td className="whitespace-nowrap px-2 py-1.5 text-right text-ink-500">
                      {formatNumber(item.tokens)}
                    </td>
                    <td className="whitespace-nowrap px-2 py-1.5 text-right text-ink-500">
                      {formatCost(item.cost_yuan)}
                    </td>
                    <td className="px-2 py-1.5 text-center text-[10px] text-ink-500">
                      {item.llm_enabled ? "LLM" : "确定性"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <div className="flex items-center justify-between border-t border-ink-100 px-3 py-2 text-[10.5px] text-ink-500">
            <span>
              第 {total === 0 ? 0 : offset + 1}–{Math.min(offset + PAGE_SIZE, total)} 条
            </span>
            <div className="flex gap-1">
              <button
                type="button"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                className="rounded border border-ink-200 px-2 py-0.5 disabled:opacity-40"
              >
                上一页
              </button>
              <button
                type="button"
                disabled={offset + PAGE_SIZE >= total}
                onClick={() => setOffset(offset + PAGE_SIZE)}
                className="rounded border border-ink-200 px-2 py-0.5 disabled:opacity-40"
              >
                下一页
              </button>
            </div>
          </div>
        </div>

        <aside className="w-[320px] shrink-0 border-l border-ink-200 bg-white p-3">
          <div className="text-[12px] font-medium text-ink-700">两次审查对比</div>
          <div className="mt-1 text-[10.5px] leading-relaxed text-ink-400">
            勾选表中任意两条记录（先旧后新），看新增 / 已修复 / 持续存在的问题。
          </div>

          <div className="mt-3 space-y-1.5 text-[11px]">
            {[0, 1].map((index) => (
              <div key={index} className="flex items-center gap-1.5">
                <span className="w-8 text-[10px] text-ink-400">{index === 0 ? "旧" : "新"}</span>
                <select
                  value={selected[index] ?? ""}
                  onChange={(event) => {
                    const value = Number(event.target.value);
                    setSelected((current) => {
                      const next = [...current];
                      if (index === 0) next[0] = value;
                      else next[1] = value;
                      return next.filter(Boolean);
                    });
                  }}
                  className="min-w-0 flex-1 rounded border border-ink-200 bg-white px-1.5 py-1 text-[10.5px]"
                >
                  <option value="">未选择</option>
                  {items.map((item) => (
                    <option key={item.id} value={item.id}>
                      #{item.id} · {item.score} 分 · {item.created_at.slice(5, 16)}
                    </option>
                  ))}
                </select>
              </div>
            ))}
          </div>

          {compare.data ? (
            <div className="mt-3 space-y-2">
              <div className="rounded border border-ink-100 bg-ink-50/60 p-2 text-[11px]">
                <div className="flex items-center justify-between">
                  <span>评分</span>
                  <span className="font-medium">
                    {compare.data.before.score} → {compare.data.after.score}
                    <span
                      className={clsx(
                        "ml-1",
                        compare.data.score_delta >= 0 ? "text-emerald-600" : "text-red-600",
                      )}
                    >
                      ({compare.data.score_delta >= 0 ? "+" : ""}
                      {compare.data.score_delta})
                    </span>
                  </span>
                </div>
                <div className="mt-1 grid grid-cols-3 gap-1 text-[10px] text-ink-500">
                  <span>新增 {compare.data.counts.added}</span>
                  <span>已修复 {compare.data.counts.resolved}</span>
                  <span>持续 {compare.data.counts.persisted}</span>
                </div>
              </div>

              <CompareList title="新增问题" tone="red" items={compare.data.added} />
              <CompareList title="已修复" tone="green" items={compare.data.resolved} />
              <CompareList title="持续存在" tone="gray" items={compare.data.persisted} />
            </div>
          ) : (
            <div className="mt-3 text-[10.5px] text-ink-400">选择两条记录后显示对比结果。</div>
          )}
        </aside>
      </div>
    </div>
  );
}

function CompareList({
  title,
  tone,
  items,
}: {
  title: string;
  tone: "red" | "green" | "gray";
  items: { file: string; line_start: number; title: string; severity: string; id: string }[];
}) {
  if (!items?.length) return null;
  const color =
    tone === "red" ? "text-red-700" : tone === "green" ? "text-emerald-700" : "text-ink-600";
  return (
    <div>
      <div className={clsx("mb-1 text-[10px] font-medium", color)}>
        {title}（{items.length}）
      </div>
      <ul className="space-y-1">
        {items.slice(0, 12).map((item) => (
          <li key={item.id} className="text-[10.5px] leading-snug">
            <span className={clsx("mr-1 rounded px-1", severityMeta(item.severity).bg, severityMeta(item.severity).text)}>
              {severityMeta(item.severity).label}
            </span>
            <span className="text-ink-700">{item.title}</span>
            <span className="ml-1 font-mono text-[9.5px] text-ink-400">
              {item.file}:{item.line_start}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

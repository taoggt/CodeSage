import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";

import { api } from "../api/client";
import { useEvalDatasets, useEvalDetail, useEvalResults } from "../api/queries";
import { EmptyState } from "../components/EmptyState";
import { ToolFeed } from "../components/ToolFeed";
import { formatCost, formatPercent, relativeTime } from "../lib/format";
import { useJobStore } from "../stores/job";
import { useJobStream } from "../hooks/useJobStream";

export function Evals() {
  const queryClient = useQueryClient();
  const datasets = useEvalDatasets();
  const results = useEvalResults();
  const [dataset, setDataset] = useState("");
  const [tag, setTag] = useState("baseline");
  const [mock, setMock] = useState(true);
  const [sample, setSample] = useState("");
  const [selectedTag, setSelectedTag] = useState<string | null>(null);
  const [compareA, setCompareA] = useState("");
  const [compareB, setCompareB] = useState("");
  const [notice, setNotice] = useState("");

  const jobId = useJobStore((state) => state.jobId);
  const jobStatus = useJobStore((state) => state.status);
  const jobResult = useJobStore((state) => state.result);
  const counters = useJobStore((state) => state.counters);
  const begin = useJobStore((state) => state.begin);
  const clear = useJobStore((state) => state.clear);
  useJobStream(jobId);

  const detail = useEvalDetail(selectedTag);
  const compare = useQuery({
    queryKey: ["eval-compare", compareA, compareB],
    queryFn: () =>
      api<any>(`/eval/compare?a=${encodeURIComponent(compareA)}&b=${encodeURIComponent(compareB)}`),
    enabled: !!compareA && !!compareB && compareA !== compareB,
    retry: 0,
  });

  useEffect(() => {
    if (!dataset && datasets.data?.items?.length) {
      setDataset(datasets.data.items[0].path);
    }
  }, [dataset, datasets.data]);

  useEffect(() => {
    if (jobStatus === "done" && jobResult) {
      queryClient.invalidateQueries({ queryKey: ["eval-results"] });
      // 跑完自动打开这次的结果，省一次点击
      const finishedTag = jobResult?.tag;
      if (finishedTag) setSelectedTag(finishedTag);
    }
  }, [jobStatus, jobResult, queryClient]);

  const busy = ["queued", "running", "cancelling"].includes(jobStatus);
  const items = results.data?.items ?? [];

  async function start() {
    setNotice("");
    if (!dataset) {
      setNotice("请选择数据集目录");
      return;
    }
    try {
      const response = await api<{ job_id: string }>("/eval", {
        method: "POST",
        body: JSON.stringify({
          dataset,
          tag: tag.trim() || "baseline",
          mock,
          sample: sample ? Number(sample) : null,
          profile: "full",
        }),
      });
      begin(response.job_id);
    } catch (error) {
      setNotice((error as Error).message);
    }
  }

  return (
    <div className="flex h-full">
      <aside className="flex w-[300px] shrink-0 flex-col border-r border-ink-200 bg-white">
        <div className="border-b border-ink-100 p-3">
          <div className="text-[12px] font-medium text-ink-700">运行评测</div>
          <div className="mt-0.5 text-[10.5px] leading-relaxed text-ink-400">
            与 CLI <code className="rounded bg-ink-100 px-1">eval</code> 完全同一条链路，
            结果写到 <code className="rounded bg-ink-100 px-1">.codesage-eval/</code>。
          </div>

          <label className="mt-2 block text-[10.5px] text-ink-500">
            数据集
            <select
              value={dataset}
              onChange={(event) => setDataset(event.target.value)}
              className="mt-0.5 w-full rounded border border-ink-200 bg-white px-1.5 py-1 text-[11px]"
            >
              {(datasets.data?.items ?? []).map((item) => (
                <option key={item.path} value={item.path}>
                  {item.name}（{item.cases} 例）
                </option>
              ))}
              {(datasets.data?.items ?? []).length === 0 && <option value="">未发现数据集</option>}
            </select>
          </label>

          <div className="mt-1.5 flex gap-1.5">
            <input
              value={tag}
              onChange={(event) => setTag(event.target.value)}
              placeholder="tag"
              className="min-w-0 flex-1 rounded border border-ink-200 px-2 py-1 text-[11px] outline-none focus:border-blue-400"
            />
            <input
              value={sample}
              onChange={(event) => setSample(event.target.value)}
              placeholder="前 N 例"
              className="w-[70px] rounded border border-ink-200 px-2 py-1 text-[11px] outline-none focus:border-blue-400"
            />
          </div>

          <label className="mt-2 flex cursor-pointer items-start gap-2">
            <input
              type="checkbox"
              checked={mock}
              onChange={(event) => setMock(event.target.checked)}
              className="mt-[2px] h-3 w-3 accent-ink-900"
            />
            <span className="text-[10.5px] leading-snug text-ink-600">
              mock 模式（只跑确定性层，无需 API Key）
            </span>
          </label>

          <button
            type="button"
            onClick={start}
            disabled={busy}
            className="mt-2 w-full rounded bg-ink-900 py-1.5 text-[11.5px] text-white hover:bg-ink-800 disabled:opacity-50"
          >
            {busy ? "评测进行中…" : "运行评测"}
          </button>
          {notice && <div className="mt-1.5 text-[10.5px] text-ink-500">{notice}</div>}
        </div>

        {busy ? (
          <div className="space-y-3 p-3">
            <EvalProgress
              index={counters.case_index || 0}
              total={counters.case_total || 0}
              recall={counters.recall || 0}
            />
            <ToolFeed />
            <button
              type="button"
              onClick={() => jobId && api(`/jobs/${jobId}/cancel`, { method: "POST" })}
              className="w-full rounded border border-ink-200 py-1 text-[11px] text-ink-600 hover:bg-ink-50"
            >
              停止评测
            </button>
          </div>
        ) : (
          <div className="min-h-0 flex-1 overflow-y-auto">
            <div className="flex items-center gap-2 px-3 py-2 text-[10.5px] text-ink-400">
              历史结果
              {jobStatus === "done" && (
                <button type="button" onClick={clear} className="ml-auto hover:text-ink-700">
                  收起本次结果
                </button>
              )}
            </div>
            {items.length === 0 ? (
              <div className="px-3 text-[11px] text-ink-400">还没有评测结果</div>
            ) : (
              <ul className="divide-y divide-ink-100">
                {items.map((item) => (
                  <li key={item.tag}>
                    <button
                      type="button"
                      onClick={() => setSelectedTag(item.tag)}
                      className={clsx(
                        "w-full px-3 py-2 text-left",
                        selectedTag === item.tag ? "bg-blue-50" : "hover:bg-ink-50",
                      )}
                    >
                      <div className="flex items-center gap-1.5">
                        <span className="truncate text-[11.5px] font-medium text-ink-800">
                          {item.tag}
                        </span>
                        <span className="rounded bg-ink-100 px-1 text-[9.5px] text-ink-500">
                          {item.mock ? "mock" : "LLM"}
                        </span>
                        <span className="ml-auto text-[10px] text-ink-400">
                          {relativeTime(item.created_at)}
                        </span>
                      </div>
                      <div className="mt-0.5 flex items-center gap-2 text-[10px] text-ink-500">
                        <span>R {formatPercent(item.recall, 0)}</span>
                        <span>P {formatPercent(item.precision, 0)}</span>
                        <span>F1 {item.f1.toFixed(3)}</span>
                        <span>{item.cases} 例</span>
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </aside>

      <section className="min-w-0 flex-1 overflow-y-auto bg-white p-4">
        {detail.data ? (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-3">
              <h2 className="text-[13px] font-semibold">{detail.data.summary.tag}</h2>
              <span className="rounded bg-ink-100 px-1.5 py-0.5 text-[10px] text-ink-500">
                {detail.data.summary.mock ? "确定性层（mock）" : "完整链路"}
              </span>
              {detail.data.summary.cancelled && (
                <span className="rounded bg-amber-50 px-1.5 py-0.5 text-[10px] text-amber-700">
                  已取消（部分结果）
                </span>
              )}
              <span className="text-[10.5px] text-ink-400">
                {detail.data.summary.duration_sec}s · {formatCost(detail.data.summary.total_cost_yuan)}
              </span>
            </div>

            <div className="grid grid-cols-2 gap-2 lg:grid-cols-5">
              <Metric label="检出率 Recall" value={formatPercent(detail.data.summary.recall)} tone="emerald" />
              <Metric label="精确率 Precision" value={formatPercent(detail.data.summary.precision)} tone="blue" />
              <Metric
                label="误报率（=1-P 近似）"
                value={formatPercent(detail.data.summary.false_positive_rate ?? 1 - detail.data.summary.precision)}
                tone="amber"
              />
              <Metric label="F1" value={Number(detail.data.summary.f1).toFixed(3)} tone="ink" />
              <Metric label="案例数" value={String(detail.data.summary.cases)} tone="ink" />
            </div>

            <p className="rounded border border-ink-100 bg-ink-50/60 px-2.5 py-2 text-[10.5px] leading-relaxed text-ink-500">
              口径说明：软匹配 = 文件名相同 + 行区间重叠 ≥30% + 至少命中 1 个关键词；
              Precision 为近似值（≠ 真实误报率），所以“误报率”用 1 − Precision 表示。
              数据集当前为 5 例种子集，指标用于回归对比，不代表生产效果。
            </p>

            <div>
              <div className="mb-1 text-[11px] font-medium text-ink-700">逐案例</div>
              <table className="w-full border-collapse text-[11px]">
                <thead className="bg-ink-50 text-[10px] text-ink-500">
                  <tr>
                    <th className="px-2 py-1 text-left font-medium">案例</th>
                    <th className="px-2 py-1 text-left font-medium">类别</th>
                    <th className="px-2 py-1 text-center font-medium">结果</th>
                    <th className="px-2 py-1 text-right font-medium">报告条数</th>
                    <th className="px-2 py-1 text-left font-medium">报了但没匹配上</th>
                  </tr>
                </thead>
                <tbody>
                  {detail.data.cases.map((item: any) => (
                    <tr key={item.case_id} className="border-b border-ink-100">
                      <td className="px-2 py-1 font-mono">{item.case_id}</td>
                      <td className="px-2 py-1">{item.category}</td>
                      <td className="px-2 py-1 text-center">
                        <span className={item.detected ? "text-emerald-600" : "text-red-600"}>
                          {item.detected ? "检出" : "漏报"}
                        </span>
                      </td>
                      <td className="px-2 py-1 text-right">{item.reported ?? 0}</td>
                      <td className="max-w-[320px] truncate px-2 py-1 text-[10px] text-ink-500">
                        {(item.reported_titles || []).join(" / ") || "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="rounded border border-ink-100 p-3">
              <div className="text-[11px] font-medium text-ink-700">A/B 对比</div>
              <div className="mt-1.5 flex flex-wrap items-center gap-2">
                <select
                  value={compareA}
                  onChange={(event) => setCompareA(event.target.value)}
                  className="rounded border border-ink-200 bg-white px-1.5 py-1 text-[10.5px]"
                >
                  <option value="">基线 tag</option>
                  {items.map((item) => (
                    <option key={item.tag} value={item.tag}>
                      {item.tag}
                    </option>
                  ))}
                </select>
                <span className="text-[10px] text-ink-400">→</span>
                <select
                  value={compareB}
                  onChange={(event) => setCompareB(event.target.value)}
                  className="rounded border border-ink-200 bg-white px-1.5 py-1 text-[10.5px]"
                >
                  <option value="">对比 tag</option>
                  {items.map((item) => (
                    <option key={item.tag} value={item.tag}>
                      {item.tag}
                    </option>
                  ))}
                </select>
                {compare.error && (
                  <span className="text-[10px] text-red-600">{(compare.error as Error).message}</span>
                )}
              </div>

              {compare.data && (
                <div className="mt-2 grid gap-2 lg:grid-cols-2">
                  <div>
                    <div className="mb-1 text-[10px] uppercase tracking-wide text-ink-400">指标变化</div>
                    <table className="w-full text-[10.5px]">
                      <tbody>
                        {compare.data.metrics
                          .filter((row: any) => ["recall", "precision", "f1", "total_cost_yuan"].includes(row.metric))
                          .map((row: any) => (
                            <tr key={row.metric} className="border-b border-ink-100">
                              <td className="py-0.5 text-ink-500">{row.metric}</td>
                              <td className="py-0.5 text-right">{row.a}</td>
                              <td className="py-0.5 text-right">{row.b}</td>
                              <td
                                className={clsx(
                                  "py-0.5 text-right",
                                  row.delta > 0 ? "text-emerald-600" : row.delta < 0 ? "text-red-600" : "text-ink-400",
                                )}
                              >
                                {row.delta > 0 ? "+" : ""}
                                {row.delta}
                              </td>
                            </tr>
                          ))}
                      </tbody>
                    </table>
                  </div>
                  <div>
                    <div className="mb-1 text-[10px] uppercase tracking-wide text-ink-400">逐案例回归</div>
                    <div className="text-[10.5px] text-ink-600">
                      回归 {compare.data.regressions.length} 例 · 改善 {compare.data.improvements.length} 例
                    </div>
                    <ul className="mt-1 space-y-0.5 text-[10px]">
                      {compare.data.regressions.map((item: any) => (
                        <li key={item.case_id} className="text-red-600">
                          ↘ {item.case_id} 从检出变漏报
                        </li>
                      ))}
                      {compare.data.improvements.map((item: any) => (
                        <li key={item.case_id} className="text-emerald-600">
                          ↗ {item.case_id} 从漏报变检出
                        </li>
                      ))}
                    </ul>
                  </div>
                </div>
              )}
            </div>
          </div>
        ) : (
          <EmptyState
            title="选择一次评测结果"
            hint="左侧列出 .codesage-eval/ 下的所有 tag；运行一次 mock 评测即可看到检出率、误报率与成本。"
          />
        )}
      </section>
    </div>
  );
}

function Metric({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: "emerald" | "blue" | "amber" | "ink";
}) {
  const color =
    tone === "emerald"
      ? "text-emerald-700 border-emerald-200 bg-emerald-50"
      : tone === "blue"
        ? "text-blue-700 border-blue-200 bg-blue-50"
        : tone === "amber"
          ? "text-amber-700 border-amber-200 bg-amber-50"
          : "text-ink-800 border-ink-200 bg-ink-50";
  return (
    <div className={clsx("rounded border px-2.5 py-2", color)}>
      <div className="text-[9.5px] opacity-80">{label}</div>
      <div className="text-[16px] font-semibold leading-tight">{value}</div>
    </div>
  );
}

function EvalProgress({
  index,
  total,
  recall,
}: {
  index: number;
  total: number;
  recall: number;
}) {
  const ratio = total ? Math.min(1, index / total) : 0;
  return (
    <div>
      <div className="flex items-baseline justify-between text-[10.5px] text-ink-500">
        <span>评测进度</span>
        <span>
          {index}/{total || "?"}
        </span>
      </div>
      <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-ink-200">
        <div
          className="h-full rounded-full bg-blue-500 transition-all"
          style={{ width: `${Math.max(2, ratio * 100)}%` }}
        />
      </div>
      <div className="mt-1 text-[10px] text-ink-400">当前累计检出率 {formatPercent(recall, 0)}</div>
    </div>
  );
}

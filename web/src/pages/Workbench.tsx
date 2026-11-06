import { useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import clsx from "clsx";

import { api, qs } from "../api/client";
import { useFile, useReview, useWorkspaceStatus } from "../api/queries";
import type { WorkspaceInfo } from "../api/types";
import { ScoreRing, SeverityBadge } from "../components/Badges";
import { CodeViewer } from "../components/CodeViewer";
import { EmptyState } from "../components/EmptyState";
import { FileTree } from "../components/FileTree";
import { ProgressTimeline, TokenGauge } from "../components/ProgressTimeline";
import { ToolFeed } from "../components/ToolFeed";
import { WorkspacePicker } from "../components/WorkspacePicker";
import { severityMeta } from "../lib/severity";
import { samePath } from "../lib/recent";
import { useJobStore } from "../stores/job";
import { useWorkspaceStore } from "../stores/workspace";
import { useJobStream } from "../hooks/useJobStream";

const ACTIVE_STATUS = new Set(["queued", "running", "cancelling"]);

export function Workbench() {
  const queryClient = useQueryClient();
  const { root, name, currentFile, gitBranch, changedCount, setWorkspace, setFile, hydrate } =
    useWorkspaceStore();

  const [target, setTarget] = useState(".");
  const [profile, setProfile] = useState("full");
  const [useLlm, setUseLlm] = useState(true);
  const [changedOnly, setChangedOnly] = useState(false);
  const [maxTurns, setMaxTurns] = useState(12);
  const [reviewId, setReviewId] = useState<number | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [tab, setTab] = useState<"code" | "preview">("code");
  const [preview, setPreview] = useState<any>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [workspaceNotice, setWorkspaceNotice] = useState("");
  const [actionNotice, setActionNotice] = useState("");

  const jobId = useJobStore((state) => state.jobId);
  const jobStatus = useJobStore((state) => state.status);
  const jobResult = useJobStore((state) => state.result);
  const jobError = useJobStore((state) => state.error);
  const begin = useJobStore((state) => state.begin);
  const clearJob = useJobStore((state) => state.clear);

  useJobStream(jobId);

  const status = useWorkspaceStatus(root);
  const file = useFile(root, currentFile);
  const review = useReview(reviewId);

  useEffect(() => {
    hydrate();
    useJobStore.getState().hydrate();
  }, [hydrate]);

  useEffect(() => {
    if (!reviewId && status.data?.latest_review?.id) {
      setReviewId(status.data.latest_review.id);
    }
  }, [reviewId, status.data]);

  useEffect(() => {
    if (jobStatus === "done" && jobResult?.review_id) {
      setReviewId(jobResult.review_id);
      queryClient.invalidateQueries({ queryKey: ["workspace-status"] });
      queryClient.invalidateQueries({ queryKey: ["reviews"] });
      queryClient.invalidateQueries({ queryKey: ["stats"] });
    }
  }, [jobStatus, jobResult, queryClient]);

  // 切换工作区时，旧报告的 findings 会在一次渲染里仍然存在；
  // 若不过滤，会拿旧文件名去新根目录取文件（404），并让右上角显示错误的文件名。
  const reviewProject = review.data?.review?.project ?? "";
  const reviewMatchesRoot = !!root && (!reviewProject || samePath(reviewProject, root));
  const allFindings = reviewMatchesRoot ? (review.data?.findings ?? []) : [];
  const fileFindings = useMemo(
    () => (currentFile ? allFindings.filter((item) => item.file === currentFile) : []),
    [allFindings, currentFile],
  );

  // 拿到报告后自动跳到"问题最多"的文件，省去用户找
  useEffect(() => {
    if (!reviewMatchesRoot || currentFile || allFindings.length === 0) return;
    const counter = new Map<string, number>();
    for (const finding of allFindings) {
      counter.set(finding.file, (counter.get(finding.file) || 0) + 1);
    }
    const first = [...counter.entries()].sort((a, b) => b[1] - a[1])[0];
    if (first) setFile(first[0]);
  }, [reviewMatchesRoot, allFindings, currentFile, setFile]);

  const decorations = useMemo(
    () =>
      fileFindings.map((finding) => ({
        id: finding.id,
        line_start: finding.line_start,
        line_end: finding.line_end,
        severity: finding.severity,
      })),
    [fileFindings],
  );

  const active = fileFindings.find((finding) => finding.id === activeId) || null;
  const busy = ACTIVE_STATUS.has(jobStatus);
  const latest = status.data?.latest_review ?? null;

  async function handleOpened(info: WorkspaceInfo) {
    setWorkspace(info);
    setReviewId(null);
    setActiveId(null);
    setPreview(null);
    setTab("code");
    setWorkspaceNotice(`已打开 ${info.name}（${info.files} 个可审查文件）`);
    setActionNotice("");
  }

  async function handleStart() {
    setActionNotice("");
    const absolute = /^([A-Za-z]:[\\/]|\/)/.test(target)
      ? target
      : target === "."
        ? root
        : `${root}/${target.replace(/^\.\//, "")}`;
    try {
      const response = await api<{ job_id: string }>("/jobs", {
        method: "POST",
        body: JSON.stringify({
          target: absolute,
          profile,
          use_llm: useLlm,
          changed_only: changedOnly,
          max_turns: maxTurns,
        }),
      });
      begin(response.job_id);
    } catch (error) {
      setActionNotice((error as Error).message);
    }
  }

  async function handleCancel() {
    if (!jobId) return;
    try {
      await api(`/jobs/${jobId}/cancel`, { method: "POST" });
    } catch (error) {
      setActionNotice((error as Error).message);
    }
  }

  async function handlePreview() {
    if (!root) return;
    setPreviewLoading(true);
    setTab("preview");
    try {
      const data = await api<any>(`/workspace/scan-preview${qs({ root, target: target === "." ? "" : target })}`);
      setPreview(data);
    } catch (error) {
      setPreview({ error: (error as Error).message });
    } finally {
      setPreviewLoading(false);
    }
  }

  function handleLineClick(line: number) {
    const hit = fileFindings.find(
      (finding) => line >= finding.line_start && line <= finding.line_end,
    );
    setActiveId(hit ? hit.id : null);
  }

  return (
    <div className="flex h-full">
      {/* ---------------- 左栏：工作区 + 文件树 ---------------- */}
      <aside className="flex w-[280px] shrink-0 flex-col border-r border-ink-200 bg-white">
        <div className="border-b border-ink-100 p-3">
          <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-medium text-ink-500">
            工作区
            {gitBranch && (
              <span className="rounded bg-ink-100 px-1 text-[10px] font-normal text-ink-500">
                {gitBranch}
                {changedCount ? ` · ${changedCount} 改动` : ""}
              </span>
            )}
          </div>
          <WorkspacePicker root={root} name={name} onOpened={handleOpened} />
          {workspaceNotice && (
            <div className="mt-1.5 text-[10.5px] leading-snug text-ink-500">{workspaceNotice}</div>
          )}
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto">
          <FileTree
            root={root}
            coverage={status.data?.file_coverage ?? {}}
            current={currentFile}
            onSelect={(path) => {
              setFile(path);
              setTab("code");
              setActiveId(null);
            }}
          />
        </div>

        <div className="border-t border-ink-100 p-3">
          {!root ? (
            <div className="text-[11px] text-ink-400">尚未打开工作区</div>
          ) : latest ? (
            <div>
              <div className="flex items-center gap-2">
                <ScoreRing score={latest.score} size={40} />
                <div className="min-w-0">
                  <div className="text-[11.5px] font-medium text-ink-700">
                    {latest.age_human}审查 · {latest.findings_count} 条
                  </div>
                  <div className="flex items-center gap-1 text-[10px] text-ink-400">
                    {["critical", "major", "minor"].map((key) =>
                      latest.by_severity?.[key] ? (
                        <span key={key} className="inline-flex items-center gap-0.5">
                          <span className={clsx("h-1.5 w-1.5 rounded-full", severityMeta(key).dot)} />
                          {latest.by_severity[key]}
                        </span>
                      ) : null,
                    )}
                    {latest.partial && <span className="text-amber-600">· 部分结果</span>}
                  </div>
                </div>
                <Link
                  to={`/report/${latest.id}`}
                  className="ml-auto shrink-0 rounded border border-ink-200 px-1.5 py-0.5 text-[10px] text-ink-600 hover:bg-ink-50"
                >
                  报告
                </Link>
              </div>
              {status.data?.stale && (
                <div className="mt-1.5 rounded bg-amber-50 px-1.5 py-1 text-[10px] text-amber-700">
                  {status.data.stale_files.length} 个文件在上次审查后被修改
                </div>
              )}
            </div>
          ) : (
            <div>
              <div className="text-[11px] text-ink-400">该项目还没有审查记录</div>
              {(status.data?.subproject_reviews?.length ?? 0) > 0 && (
                <div className="mt-1.5">
                  <div className="text-[10px] text-ink-400">但这些子目录审过：</div>
                  <ul className="mt-1 space-y-0.5">
                    {status.data!.subproject_reviews!.map((item) => (
                      <li key={item.id}>
                        <Link
                          to={`/report/${item.id}`}
                          className="flex items-center gap-1 text-[10.5px] text-blue-600 hover:underline"
                          title={item.project}
                        >
                          <span className="truncate">{item.relative}/</span>
                          <span className="ml-auto shrink-0 text-ink-400">
                            {item.score} 分 · {item.age_human}
                          </span>
                        </Link>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>
      </aside>

      {/* ---------------- 中栏：代码 / 预检 ---------------- */}
      <section className="flex min-w-0 flex-1 flex-col bg-white">
        <div className="flex h-9 shrink-0 items-center gap-1 border-b border-ink-200 px-2">
          <TabButton active={tab === "code"} onClick={() => setTab("code")}>
            代码
          </TabButton>
          <TabButton active={tab === "preview"} onClick={handlePreview}>
            扫描预检
          </TabButton>
          <div className="ml-auto flex items-center gap-2 text-[10.5px] text-ink-400">
            {currentFile && <span className="font-mono">{currentFile}</span>}
            {fileFindings.length > 0 && (
              <span className="rounded bg-ink-100 px-1.5 py-0.5">{fileFindings.length} 条</span>
            )}
          </div>
        </div>

        <div className="min-h-0 flex-1">
          {tab === "code" ? (
            file.data ? (
              <CodeViewer
                value={file.data.content}
                findings={decorations}
                activeId={activeId}
                onSelectLine={handleLineClick}
                language={file.data.language}
                placeholder="从左侧选择文件查看源码"
              />
            ) : (
              <EmptyState
                title={root ? "选择左侧文件开始" : "先选择一个工作区"}
                hint={
                  root
                    ? "代码视图会把历史审查发现按行高亮；点色块可在下方查看详情。"
                    : "点左上角「选择工作区」：可直接调起系统文件夹对话框，也能在网页里逐层浏览。审查只读取代码，不会修改任何文件。"
                }
              />
            )
          ) : (
            <ScanPreview data={preview} loading={previewLoading} />
          )}
        </div>

        {tab === "code" && (active || fileFindings.length > 0) && (
          <div className="max-h-[38%] shrink-0 overflow-y-auto border-t border-ink-200 bg-white">
            {active ? (
              <div className="px-4 py-3">
                <div className="flex items-center gap-2">
                  <SeverityBadge severity={active.severity} />
                  <span className="text-[13px] font-medium">{active.title}</span>
                  <span className="ml-auto font-mono text-[10.5px] text-ink-400">
                    {active.file}:{active.line_start}
                  </span>
                </div>
                {active.description && (
                  <p className="mt-1.5 whitespace-pre-wrap text-[12px] leading-relaxed text-ink-600">
                    {active.description}
                  </p>
                )}
                {active.suggestion && (
                  <p className="mt-2 rounded border border-emerald-100 bg-emerald-50/60 px-2 py-1.5 text-[11.5px] text-emerald-900">
                    建议：{active.suggestion}
                  </p>
                )}
                {reviewId && (
                  <Link
                    to={`/report/${reviewId}`}
                    className="mt-2 inline-block text-[10.5px] text-blue-600 hover:underline"
                  >
                    在完整报告中查看 →
                  </Link>
                )}
              </div>
            ) : (
              <div className="px-4 py-3 text-[11.5px] text-ink-500">
                该文件有 {fileFindings.length} 条历史发现，点击代码中的高亮行查看详情。
              </div>
            )}
          </div>
        )}
      </section>

      {/* ---------------- 右栏：审查控制台 ---------------- */}
      <aside className="flex w-[380px] shrink-0 flex-col border-l border-ink-200 bg-white">
        <div className="border-b border-ink-100 px-4 py-3">
          <div className="text-[12px] font-medium text-ink-700">审查控制台</div>
          <div className="mt-0.5 text-[10.5px] text-ink-400">
            {busy ? `${labelForStatus(jobStatus)} · ${jobResult?.duration ?? ""}` : "配置一次审查"}
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
          {busy || jobStatus === "cancelling" ? (
            <div className="space-y-4">
              <ProgressTimeline />
              <TokenGauge />
              <div>
                <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-ink-400">
                  Agent 工作日志
                </div>
                <ToolFeed />
              </div>
              <button
                type="button"
                onClick={handleCancel}
                className="w-full rounded border border-ink-200 py-1.5 text-[11.5px] text-ink-600 hover:border-red-300 hover:bg-red-50 hover:text-red-700"
              >
                停止审查
              </button>
              <p className="text-[10px] leading-relaxed text-ink-400">
                取消是协作式的：正在进行的 LLM 请求会先返回（最长约 120 秒），之后不再开始新一轮。
              </p>
            </div>
          ) : jobStatus === "failed" ? (
            <div className="space-y-3">
              <div className="rounded border border-red-200 bg-red-50 px-2.5 py-2 text-[11.5px] text-red-700">
                {jobError?.message || "审查失败"}
              </div>
              <button
                type="button"
                onClick={clearJob}
                className="w-full rounded border border-ink-200 py-1.5 text-[11.5px] text-ink-600 hover:bg-ink-50"
              >
                返回配置
              </button>
            </div>
          ) : (
            <div className="space-y-3.5">
              {jobStatus === "done" && jobResult && (
                <ResultCard
                  score={jobResult.score ?? 0}
                  findings={jobResult.findings ?? 0}
                  reviewId={jobResult.review_id}
                  onDismiss={clearJob}
                  onOpen={() => {
                    if (jobResult.review_id) setReviewId(jobResult.review_id);
                  }}
                />
              )}

              <Field label="审查目标（相对工作区）">
                <input
                  value={target}
                  onChange={(event) => setTarget(event.target.value)}
                  className="w-full rounded border border-ink-200 px-2 py-1.5 font-mono text-[11.5px] outline-none focus:border-blue-400"
                />
              </Field>

              <Field label="审查角色">
                <select
                  value={profile}
                  onChange={(event) => setProfile(event.target.value)}
                  className="w-full rounded border border-ink-200 bg-white px-2 py-1.5 text-[11.5px] outline-none focus:border-blue-400"
                >
                  <option value="full">full · 全面审查</option>
                  <option value="security">security · 安全专项</option>
                  <option value="performance">performance · 性能专项</option>
                </select>
              </Field>

              <div className="space-y-1.5 rounded border border-ink-100 bg-ink-50/60 p-2.5">
                <Check
                  checked={useLlm}
                  onChange={setUseLlm}
                  label="启用 LLM 深度审查"
                  hint="关闭后只跑确定性分析（零 Token、无需 Key）"
                />
                <Check
                  checked={changedOnly}
                  onChange={setChangedOnly}
                  label="只审 git 变更"
                  hint="对比 HEAD，仅审查改动涉及的文件"
                />
              </div>

              <Field label={`最大轮次：${maxTurns}`}>
                <input
                  type="range"
                  min={2}
                  max={30}
                  value={maxTurns}
                  onChange={(event) => setMaxTurns(Number(event.target.value))}
                  className="w-full accent-ink-900"
                />
              </Field>

              <button
                type="button"
                onClick={handleStart}
                disabled={!root}
                className="w-full rounded bg-ink-900 py-2 text-[12.5px] font-medium text-white hover:bg-ink-800 disabled:opacity-50"
              >
                开始审查
              </button>
              <button
                type="button"
                onClick={handlePreview}
                className="w-full rounded border border-ink-200 py-1.5 text-[11.5px] text-ink-600 hover:bg-ink-50"
              >
                零成本预检：这次会审哪些文件
              </button>
              {actionNotice && (
                <div className="rounded bg-amber-50 px-2 py-1.5 text-[10.5px] leading-snug text-amber-700">
                  {actionNotice}
                </div>
              )}
            </div>
          )}
        </div>
      </aside>
    </div>
  );
}

function TabButton({
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
        "rounded px-2.5 py-1 text-[11.5px]",
        active ? "bg-ink-100 font-medium text-ink-900" : "text-ink-500 hover:bg-ink-50",
      )}
    >
      {children}
    </button>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-[10.5px] font-medium text-ink-500">{label}</span>
      {children}
    </label>
  );
}

function Check({
  checked,
  onChange,
  label,
  hint,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
  hint?: string;
}) {
  return (
    <label className="flex cursor-pointer items-start gap-2">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-[2px] h-3 w-3 accent-ink-900"
      />
      <span className="min-w-0">
        <span className="block text-[11.5px] text-ink-700">{label}</span>
        {hint && <span className="block text-[10px] leading-snug text-ink-400">{hint}</span>}
      </span>
    </label>
  );
}

function ResultCard({
  score,
  findings,
  reviewId,
  onOpen,
  onDismiss,
}: {
  score: number;
  findings: number;
  reviewId?: number;
  onOpen: () => void;
  onDismiss: () => void;
}) {
  return (
    <div className="rounded border border-emerald-200 bg-emerald-50/50 p-3">
      <div className="flex items-center gap-2">
        <ScoreRing score={score} size={44} />
        <div className="min-w-0 flex-1">
          <div className="text-[12px] font-medium text-ink-800">审查完成</div>
          <div className="text-[10.5px] text-ink-500">{findings} 条发现 · 评分 {score}</div>
        </div>
      </div>
      <div className="mt-2 flex gap-2">
        {reviewId && (
          <Link
            to={`/report/${reviewId}`}
            onClick={onOpen}
            className="flex-1 rounded bg-ink-900 py-1.5 text-center text-[11.5px] text-white hover:bg-ink-800"
          >
            查看完整报告
          </Link>
        )}
        <button
          type="button"
          onClick={onDismiss}
          className="rounded border border-ink-200 px-2 py-1.5 text-[11.5px] text-ink-600 hover:bg-white"
        >
          继续审查
        </button>
      </div>
    </div>
  );
}

function ScanPreview({ data, loading }: { data: any; loading: boolean }) {
  if (loading) {
    return <div className="p-6 text-[12px] text-ink-400">正在扫描（零 Token）…</div>;
  }
  if (!data) {
    return (
      <EmptyState
        title="扫描预检"
        hint="不调用 LLM，只跑文件收集与过滤，回答“这次到底会审哪些文件、哪些被跳过、为什么”。"
      />
    );
  }
  if (data.error) {
    return <div className="p-6 text-[12px] text-red-600">{data.error}</div>;
  }
  return (
    <div className="h-full overflow-y-auto p-4">
      <div className="flex flex-wrap items-center gap-2 text-[11.5px]">
        <span className="rounded bg-emerald-50 px-2 py-0.5 text-emerald-700">
          将审查 {data.included_count} 个文件
        </span>
        <span className="rounded bg-ink-100 px-2 py-0.5 text-ink-600">
          跳过 {data.skipped_count} 个
        </span>
        <span className="font-mono text-[10.5px] text-ink-400">{data.target}</span>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <div>
          <div className="mb-1 text-[10.5px] font-medium uppercase tracking-wide text-ink-400">
            纳入范围
          </div>
          <ul className="space-y-0.5 font-mono text-[11px] text-ink-600">
            {(data.included || []).map((item: string) => (
              <li key={item} className="truncate">
                {item}
              </li>
            ))}
          </ul>
        </div>
        <div>
          <div className="mb-1 text-[10.5px] font-medium uppercase tracking-wide text-ink-400">
            被跳过（含原因）
          </div>
          <ul className="space-y-1 text-[11px] text-ink-500">
            {(data.skipped || []).map((item: { file: string; reason: string }) => (
              <li key={`${item.file}-${item.reason}`}>
                <span className="font-mono text-ink-600">{item.file}</span>
                <span className="ml-1 text-ink-400">— {item.reason}</span>
              </li>
            ))}
            {(data.skipped || []).length === 0 && <li className="text-ink-400">无</li>}
          </ul>
        </div>
      </div>
    </div>
  );
}

function labelForStatus(status: string): string {
  if (status === "queued") return "排队中";
  if (status === "running") return "审查进行中";
  if (status === "cancelling") return "正在取消";
  if (status === "cancelled") return "已取消";
  if (status === "done") return "已完成";
  return status;
}

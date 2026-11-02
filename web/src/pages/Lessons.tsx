import { useState } from "react";
import clsx from "clsx";

import {
  useAddMissed,
  useDeleteLesson,
  useFeedbackList,
  useLessonPreview,
  useLessons,
  useMineLessons,
  useResetMined,
} from "../api/queries";
import { EmptyState } from "../components/EmptyState";
import { relativeTime } from "../lib/format";
import { useWorkspaceStore } from "../stores/workspace";

const KIND_LABEL: Record<string, string> = {
  false_positive: "误报",
  false_negative: "漏报",
};

export function Lessons() {
  const root = useWorkspaceStore((state) => state.root);
  const [kind, setKind] = useState<string>("");
  const [tab, setTab] = useState<"lessons" | "inbox">("lessons");
  const [toast, setToast] = useState("");
  const [form, setForm] = useState({ file: "", line: "", title: "", category: "", note: "" });

  const lessons = useLessons(root, kind || undefined);
  const preview = useLessonPreview(root);
  const feedback = useFeedbackList(root);
  const mine = useMineLessons();
  const resetMined = useResetMined();
  const addMissed = useAddMissed();
  const remove = useDeleteLesson();
  const [confirmReset, setConfirmReset] = useState(false);

  const stats = lessons.data?.stats ?? { false_positive: 0, false_negative: 0 };
  const items = lessons.data?.items ?? [];
  // 待提炼数量由服务端算（只统计 wrong/missed 且尚未提炼的反馈），
  // 而不是"反馈流水里所有 wrong/missed 的条数"——后者在提炼后仍不变，会误导。
  const pending = lessons.data?.pending ?? 0;

  async function handleMine() {
    setToast("");
    try {
      const result = await mine.mutateAsync(root);
      setToast(
        result.total
          ? `已提炼 ${result.total} 条经验（误报 ${result.false_positive} / 漏报 ${result.false_negative}），剩余 ${result.pending} 条待提炼`
          : "没有可提炼的新反馈",
      );
    } catch (error) {
      setToast((error as Error).message);
    }
  }

  async function handleReset() {
    setToast("");
    try {
      const result = await resetMined.mutateAsync(root);
      setToast(
        `已重置 ${result.reset} 条反馈的提炼标记，现在可重新提炼（共 ${result.pending} 条）`,
      );
      setConfirmReset(false);
    } catch (error) {
      setToast((error as Error).message);
    }
  }

  async function handleMissed() {
    if (!form.title.trim()) {
      setToast("请填写漏报的简要描述");
      return;
    }
    setToast("");
    try {
      const result = await addMissed.mutateAsync({
        file: form.file,
        line: form.line ? Number(form.line) : null,
        title: form.title,
        category: form.category,
        note: form.note,
        project: root,
      });
      setToast(result.id > 0 ? `已新增漏报经验 #${result.id}` : "该经验已存在（去重）");
      setForm({ file: "", line: "", title: "", category: "", note: "" });
    } catch (error) {
      setToast((error as Error).message);
    }
  }

  return (
    <div className="flex h-full flex-col">
      <div className="shrink-0 border-b border-ink-200 bg-white px-5 py-3">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-[13px] font-semibold">错题本</h1>
          <span className="rounded bg-red-50 px-1.5 py-0.5 text-[10.5px] text-red-700">
            误报 {stats.false_positive ?? 0}
          </span>
          <span className="rounded bg-amber-50 px-1.5 py-0.5 text-[10.5px] text-amber-700">
            漏报 {stats.false_negative ?? 0}
          </span>
          <span className="text-[10.5px] text-ink-400">
            作用域：{root ? `本项目 + 通用经验` : "全部项目"}
          </span>
          <div className="ml-auto flex items-center gap-2">
            <button
              type="button"
              onClick={handleMine}
              disabled={mine.isPending || pending === 0}
              title={
                pending === 0
                  ? "没有待提炼的反馈"
                  : "把未提炼的反馈批量转成错题本经验"
              }
              className="rounded border border-ink-200 px-2 py-1 text-[11px] text-ink-600 hover:bg-ink-50 disabled:opacity-50"
            >
              {mine.isPending ? "提炼中…" : `批量提炼反馈（${pending}）`}
            </button>
            {confirmReset ? (
              <span className="flex items-center gap-1.5">
                <span className="text-[10px] text-amber-700">确认恢复历史反馈？</span>
                <button
                  type="button"
                  onClick={handleReset}
                  disabled={resetMined.isPending}
                  className="rounded border border-amber-300 bg-amber-50 px-1.5 py-0.5 text-[10px] text-amber-700 hover:bg-amber-100 disabled:opacity-50"
                >
                  确认
                </button>
                <button
                  type="button"
                  onClick={() => setConfirmReset(false)}
                  className="rounded px-1.5 py-0.5 text-[10px] text-ink-400 hover:bg-ink-100"
                >
                  取消
                </button>
              </span>
            ) : (
              <button
                type="button"
                onClick={() => setConfirmReset(true)}
                title="删除经验后若想重新提炼，先重置标记再点提炼"
                className="rounded px-1.5 py-0.5 text-[10px] text-ink-400 hover:bg-ink-100 hover:text-ink-700"
              >
                重新提炼历史反馈
              </button>
            )}
          </div>
        </div>
        {toast && (
          <div className="mt-2 rounded bg-ink-900 px-2.5 py-1.5 text-[11px] text-white">{toast}</div>
        )}
      </div>

      <div className="flex min-h-0 flex-1">
        <div className="flex min-w-0 flex-1 flex-col border-r border-ink-200 bg-white">
          <div className="flex shrink-0 items-center gap-1 border-b border-ink-100 px-3 py-2">
            <Tab active={tab === "lessons"} onClick={() => setTab("lessons")}>
              经验库 {items.length}
            </Tab>
            <Tab active={tab === "inbox"} onClick={() => setTab("inbox")}>
              反馈流水 {feedback.data?.total ?? 0}
            </Tab>
            {tab === "lessons" && (
              <div className="ml-auto flex items-center gap-1">
                <Chip active={kind === ""} onClick={() => setKind("")}>
                  全部
                </Chip>
                <Chip active={kind === "false_positive"} onClick={() => setKind("false_positive")}>
                  误报
                </Chip>
                <Chip active={kind === "false_negative"} onClick={() => setKind("false_negative")}>
                  漏报
                </Chip>
              </div>
            )}
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto">
            {tab === "lessons" ? (
              items.length === 0 ? (
                <EmptyState
                  title="错题本还是空的"
                  hint="在报告页把一条发现标为「误报」，或在这里手动补一条漏报，就会生成第一条经验。经验会在下次审查时注入 system prompt。"
                />
              ) : (
                <ul className="divide-y divide-ink-100">
                  {items.map((lesson) => (
                    <li key={lesson.id} className="px-4 py-2.5">
                      <div className="flex items-center gap-2">
                        <span
                          className={clsx(
                            "rounded px-1.5 py-0.5 text-[10px]",
                            lesson.kind === "false_positive"
                              ? "bg-red-50 text-red-700"
                              : "bg-amber-50 text-amber-700",
                          )}
                        >
                          {KIND_LABEL[lesson.kind] || lesson.kind}
                        </span>
                        {lesson.pattern && (
                          <span className="rounded bg-ink-100 px-1 text-[10px] text-ink-500">
                            {lesson.pattern}
                          </span>
                        )}
                        <span className="text-[10px] text-ink-400">
                          #{lesson.id} · {relativeTime(lesson.created_at)}
                        </span>
                        <button
                          type="button"
                          onClick={() => remove.mutate(lesson.id)}
                          className="ml-auto text-[10px] text-ink-400 hover:text-red-600"
                        >
                          删除
                        </button>
                      </div>
                      <p className="mt-1 text-[12px] leading-relaxed text-ink-700">{lesson.content}</p>
                      <div className="mt-1 truncate font-mono text-[9.5px] text-ink-400">
                        {lesson.project || "（通用，对所有项目生效）"}
                      </div>
                    </li>
                  ))}
                </ul>
              )
            ) : (feedback.data?.items ?? []).length === 0 ? (
              <EmptyState title="还没有反馈" hint="报告页的「误报 / 确认」按钮会写入这里。" />
            ) : (
              <ul className="divide-y divide-ink-100">
                {(feedback.data?.items ?? []).map((item: any) => (
                  <li key={item.id} className="flex items-start gap-2 px-4 py-2">
                    <span
                      className={clsx(
                        "mt-0.5 rounded px-1.5 py-0.5 text-[10px]",
                        item.verdict === "wrong"
                          ? "bg-red-50 text-red-700"
                          : item.verdict === "missed"
                            ? "bg-amber-50 text-amber-700"
                            : "bg-emerald-50 text-emerald-700",
                      )}
                    >
                      {item.verdict === "wrong" ? "误报" : item.verdict === "missed" ? "漏报" : "确认"}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[11.5px] text-ink-700">
                        {item.title || item.finding_id}
                      </div>
                      <div className="truncate font-mono text-[9.5px] text-ink-400">
                        {item.file || "—"} · {item.created_at}
                      </div>
                      {item.note && <div className="mt-0.5 text-[10.5px] text-ink-500">{item.note}</div>}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>

        <aside className="w-[380px] shrink-0 overflow-y-auto bg-white p-4">
          <div className="text-[12px] font-medium text-ink-700">下次审查会注入的内容</div>
          <div className="mt-1 text-[10.5px] leading-relaxed text-ink-400">
            这是 <code className="rounded bg-ink-100 px-1">few_shot_block()</code> 的真实输出，
            会原样拼进 system prompt。只取最近 {preview.data?.limit_items ?? 6} 条、
            上限 {preview.data?.limit_chars ?? 1200} 字符。
          </div>

          <pre className="mt-2 max-h-[220px] overflow-auto whitespace-pre-wrap rounded border border-ink-200 bg-ink-50 p-2 font-mono text-[10.5px] leading-relaxed text-ink-700">
            {preview.data?.block || "（暂无经验注入）"}
          </pre>

          <div className="mt-1.5 flex items-center gap-2 text-[10px]">
            <span
              className={clsx(
                preview.data?.fits === false ? "text-red-600" : "text-ink-500",
              )}
            >
              {preview.data?.chars ?? 0} / {preview.data?.limit_chars ?? 1200} 字符
            </span>
            <span className="text-ink-400">
              候选 {preview.data?.candidates ?? 0} 条，实际注入 {preview.data?.injected?.length ?? 0} 条
            </span>
          </div>

          {preview.data?.truncated_by_chars && (
            <div className="mt-1.5 rounded bg-amber-50 px-2 py-1.5 text-[10.5px] leading-relaxed text-amber-700">
              字符数已达上限，部分经验不会被注入。删掉不重要的经验，或把说明写短一点。
            </div>
          )}

          <div className="mt-5 border-t border-ink-100 pt-3">
            <div className="text-[12px] font-medium text-ink-700">手动补一条漏报</div>
            <div className="mt-1 text-[10.5px] leading-relaxed text-ink-400">
              漏报的问题在报告里根本不存在，用 finding_id 反馈很别扭——这里直接按位置记录。
            </div>

            <div className="mt-2 grid grid-cols-2 gap-1.5">
              <input
                value={form.file}
                onChange={(event) => setForm({ ...form, file: event.target.value })}
                placeholder="文件（相对路径）"
                className="col-span-2 rounded border border-ink-200 px-2 py-1 font-mono text-[11px] outline-none focus:border-blue-400"
              />
              <input
                value={form.line}
                onChange={(event) => setForm({ ...form, line: event.target.value })}
                placeholder="行号"
                className="rounded border border-ink-200 px-2 py-1 text-[11px] outline-none focus:border-blue-400"
              />
              <select
                value={form.category}
                onChange={(event) => setForm({ ...form, category: event.target.value })}
                className="rounded border border-ink-200 bg-white px-1.5 py-1 text-[11px] outline-none"
              >
                <option value="">类别</option>
                <option value="security">安全</option>
                <option value="bug">潜在 Bug</option>
                <option value="performance">性能</option>
                <option value="quality">代码质量</option>
                <option value="best_practice">最佳实践</option>
              </select>
              <input
                value={form.title}
                onChange={(event) => setForm({ ...form, title: event.target.value })}
                placeholder="应该报但没报的问题"
                className="col-span-2 rounded border border-ink-200 px-2 py-1 text-[11px] outline-none focus:border-blue-400"
              />
              <textarea
                value={form.note}
                onChange={(event) => setForm({ ...form, note: event.target.value })}
                placeholder="补充说明（会写进经验文本）"
                rows={2}
                className="col-span-2 resize-none rounded border border-ink-200 px-2 py-1 text-[11px] outline-none focus:border-blue-400"
              />
            </div>
            <button
              type="button"
              onClick={handleMissed}
              disabled={addMissed.isPending}
              className="mt-2 w-full rounded bg-ink-900 py-1.5 text-[11.5px] text-white hover:bg-ink-800 disabled:opacity-50"
            >
              添加漏报经验
            </button>
          </div>
        </aside>
      </div>
    </div>
  );
}

function Tab({
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

function Chip({
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
        "rounded border px-1.5 py-0.5 text-[10px]",
        active ? "border-ink-900 bg-ink-900 text-white" : "border-ink-200 text-ink-500",
      )}
    >
      {children}
    </button>
  );
}

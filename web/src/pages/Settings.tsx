import { useState } from "react";
import type { ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";

import { api } from "../api/client";
import { useConfig, useHealth, useSaveConfig, useStats } from "../api/queries";
import type { AppConfig } from "../api/types";
import { useWorkspaceStore } from "../stores/workspace";

export function Settings() {
  const health = useHealth();
  const config = useConfig();
  const stats = useStats();
  const queryClient = useQueryClient();
  const clearWorkspace = useWorkspaceStore((state) => state.clear);
  const [notice, setNotice] = useState("");

  async function purgeEvalRows() {
    setNotice("");
    try {
      const result = await api<{ deleted: number }>("/reviews/purge", {
        method: "POST",
        body: JSON.stringify({ kind: "eval" }),
      });
      setNotice(`已清理 ${result.deleted} 条评测遗留的历史记录`);
      queryClient.invalidateQueries({ queryKey: ["reviews"] });
      queryClient.invalidateQueries({ queryKey: ["stats"] });
    } catch (error) {
      setNotice((error as Error).message);
    }
  }

  const analyzers = health.data?.analyzers ?? [];

  return (
    <div className="h-full overflow-y-auto bg-white">
      <div className="mx-auto max-w-4xl px-6 py-5">
        <h1 className="text-[14px] font-semibold">设置与自检</h1>
        <p className="mt-1 text-[11px] leading-relaxed text-ink-400">
          模型与预算可以直接在这里改，保存后立即生效并写回{" "}
          <code className="rounded bg-ink-100 px-1">codesage.toml</code>。
          API Key 与监听端口不经过网页（避免写坏密钥、改端口需要重启）。
        </p>

        {notice && (
          <div className="mt-3 rounded bg-ink-900 px-2.5 py-1.5 text-[11px] text-white">{notice}</div>
        )}

        <section className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          <Status label="存储" ok={!!health.data?.store_ok} text={health.data?.store_ok ? "可写" : "不可用"} />
          <Status
            label="LLM"
            ok={!!health.data?.llm_configured}
            text={health.data?.llm_configured ? "已配置 Key" : "未配置（可跑 --no-llm）"}
          />
          <Status
            label="前端产物"
            ok={!!health.data?.static_built}
            text={health.data?.static_built ? "已构建" : "未构建"}
          />
          <Status label="git" ok={!!health.data?.git_available} text={health.data?.git_available ? "可用" : "不可用"} />
        </section>

        <section className="mt-5">
          <h2 className="text-[12px] font-medium text-ink-700">分析器</h2>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {analyzers.map((item) => (
              <span
                key={item.name}
                className={clsx(
                  "rounded border px-2 py-0.5 text-[10.5px]",
                  item.available
                    ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                    : "border-ink-200 bg-ink-50 text-ink-400",
                )}
              >
                {item.name}
                {item.available ? "" : "（不可用）"}
              </span>
            ))}
          </div>
        </section>

        <section className="mt-5">
          <h2 className="text-[12px] font-medium text-ink-700">模型与预算</h2>
          {config.data ? (
            <ConfigForm config={config.data} />
          ) : (
            <p className="mt-1.5 text-[11px] text-ink-400">加载中…</p>
          )}
        </section>

        <section className="mt-5">
          <h2 className="text-[12px] font-medium text-ink-700">存储</h2>
          <div className="mt-1.5 rounded border border-ink-200 bg-ink-50/60 p-2.5 font-mono text-[10.5px] text-ink-600">
            {config.data?.store_path ?? health.data?.store_path ?? "—"}
          </div>
          <div className="mt-2 flex flex-wrap gap-3 text-[11px] text-ink-600">
            <span>审查记录 {stats.data?.reviews ?? 0}</span>
            <span>反馈 {stats.data?.feedback_total ?? 0}</span>
            <span>经验 {stats.data?.lessons ?? 0}</span>
            <span>待提炼 {stats.data?.pending_feedback ?? 0}</span>
          </div>
        </section>

        <section className="mt-5">
          <h2 className="text-[12px] font-medium text-ink-700">维护</h2>
          <div className="mt-1.5 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={purgeEvalRows}
              className="rounded border border-ink-200 px-2.5 py-1 text-[11px] text-ink-600 hover:bg-ink-50"
            >
              清理评测遗留的历史记录
            </button>
            <button
              type="button"
              onClick={() => {
                clearWorkspace();
                setNotice("已清除本地工作区记录，刷新后重新选择目录");
              }}
              className="rounded border border-ink-200 px-2.5 py-1 text-[11px] text-ink-600 hover:bg-ink-50"
            >
              清除本地工作区缓存
            </button>
          </div>
          <p className="mt-1.5 text-[10px] leading-relaxed text-ink-400">
            评测曾把临时目录写进 reviews 表（旧版本副作用，现已改为不落库），这条清理只删除 target 含
            <code className="mx-1 rounded bg-ink-100 px-1">codesage_eval_</code>的记录。
          </p>
        </section>

        <section className="mt-5 rounded border border-ink-100 bg-ink-50/50 p-3 text-[10.5px] leading-relaxed text-ink-500">
          <div className="font-medium text-ink-600">已知限制（不粉饰）</div>
          <ul className="mt-1 list-disc space-y-0.5 pl-4">
            <li>取消是协作式的：在途的 LLM 请求会先返回（最长约 120 秒），之后不再开始新一轮。</li>
            <li>服务必须单进程单 worker：任务状态与事件缓冲都在进程内存里。</li>
            <li>只监听 127.0.0.1、无登录；不要把它暴露到公网。</li>
            <li>评测数据集目前只有 5 个种子案例，指标用于回归对比，不代表生产效果。</li>
          </ul>
        </section>
      </div>
    </div>
  );
}

function Status({ label, ok, text }: { label: string; ok: boolean; text: string }) {
  return (
    <div
      className={clsx(
        "rounded border px-2.5 py-2",
        ok ? "border-emerald-200 bg-emerald-50" : "border-amber-200 bg-amber-50",
      )}
    >
      <div className="flex items-center gap-1.5">
        <span className={clsx("h-1.5 w-1.5 rounded-full", ok ? "bg-emerald-500" : "bg-amber-500")} />
        <span className="text-[10.5px] text-ink-600">{label}</span>
      </div>
      <div className="mt-0.5 text-[11.5px] text-ink-700">{text}</div>
    </div>
  );
}

const INPUT_CLASS =
  "w-36 rounded border border-ink-200 px-1.5 py-1 text-right font-mono text-[11px] text-ink-700 focus:border-ink-400 focus:outline-none disabled:bg-ink-50 disabled:text-ink-400";

type ConfigFormState = {
  model_strong: string;
  model_fast: string;
  max_output_tokens: number;
  context_window: number;
  budget_window: number;
  max_turns: number;
  languages: string;
};

function fromConfig(config: AppConfig): ConfigFormState {
  return {
    model_strong: config.llm.model_strong,
    model_fast: config.llm.model_fast,
    max_output_tokens: config.llm.max_output_tokens,
    context_window: config.budget.context_window,
    budget_window: config.budget.budget_window,
    max_turns: Number(config.review.max_turns ?? 12),
    languages: String(config.review.languages ?? "auto"),
  };
}

function ConfigForm({ config }: { config: AppConfig }) {
  const [form, setForm] = useState<ConfigFormState>(() => fromConfig(config));
  const [notice, setNotice] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const save = useSaveConfig();

  const lockedSet = (section: string) => new Set(config.env_locked?.[section] ?? []);
  const llmLocked = lockedSet("llm");
  const budgetLocked = lockedSet("budget");
  const reviewLocked = lockedSet("review");

  const budgetWindow = Number(form.budget_window) || 0;
  const softTokens = Math.round(budgetWindow * config.budget.soft_ratio);
  const hardTokens = Math.round(budgetWindow * config.budget.hard_ratio);
  const abortTokens = Math.round(budgetWindow * config.budget.abort_ratio);
  const exceedsModel = budgetWindow > Number(form.context_window);

  const setText = (key: "model_strong" | "model_fast" | "languages", value: string) =>
    setForm((prev) => ({ ...prev, [key]: value }));
  const setNum = (
    key: "max_output_tokens" | "context_window" | "budget_window" | "max_turns",
    value: string,
  ) => setForm((prev) => ({ ...prev, [key]: Number(value) || 0 }));

  function submit() {
    setNotice(null);
    const llm: Record<string, unknown> = {};
    if (!llmLocked.has("model_strong")) llm.model_strong = form.model_strong.trim();
    if (!llmLocked.has("model_fast")) llm.model_fast = form.model_fast.trim();
    if (!llmLocked.has("max_output_tokens")) llm.max_output_tokens = form.max_output_tokens;

    const budget: Record<string, unknown> = {};
    if (!budgetLocked.has("context_window")) budget.context_window = form.context_window;
    if (!budgetLocked.has("budget_window")) budget.budget_window = form.budget_window;

    const review: Record<string, unknown> = {};
    if (!reviewLocked.has("max_turns")) review.max_turns = form.max_turns;
    if (!reviewLocked.has("languages")) review.languages = form.languages;

    const payload: Record<string, unknown> = {};
    if (Object.keys(llm).length) payload.llm = llm;
    if (Object.keys(budget).length) payload.budget = budget;
    if (Object.keys(review).length) payload.review = review;

    if (!Object.keys(payload).length) {
      setNotice({ kind: "err", text: "可改字段都被环境变量或 .env 固定了，请先改那边" });
      return;
    }

    save.mutate(payload, {
      onSuccess: (data) => {
        setForm(fromConfig(data));
        setNotice({ kind: "ok", text: "已保存并立即生效（无需重启）" });
      },
      onError: (error) => setNotice({ kind: "err", text: (error as Error).message }),
    });
  }

  return (
    <div className="mt-1.5 rounded border border-ink-200 bg-white p-3">
      <div className="grid gap-x-6 gap-y-1 lg:grid-cols-2">
        <div>
          <Field
            label="强模型"
            hint={llmLocked.has("model_strong") ? "被环境变量固定" : "深度审查与最终总结"}
            locked={llmLocked.has("model_strong")}
          >
            <input
              value={form.model_strong}
              disabled={llmLocked.has("model_strong")}
              onChange={(event) => setText("model_strong", event.target.value)}
              className={INPUT_CLASS}
            />
          </Field>
          <Field
            label="快模型"
            hint={llmLocked.has("model_fast") ? "被环境变量固定" : "工具决策与摘要压缩"}
            locked={llmLocked.has("model_fast")}
          >
            <input
              value={form.model_fast}
              disabled={llmLocked.has("model_fast")}
              onChange={(event) => setText("model_fast", event.target.value)}
              className={INPUT_CLASS}
            />
          </Field>
          <Field
            label="单次输出上限"
            hint={llmLocked.has("max_output_tokens") ? "被环境变量固定" : "token，覆盖一份审查报告"}
            locked={llmLocked.has("max_output_tokens")}
          >
            <input
              type="number"
              value={form.max_output_tokens}
              disabled={llmLocked.has("max_output_tokens")}
              onChange={(event) => setNum("max_output_tokens", event.target.value)}
              className={INPUT_CLASS}
            />
          </Field>
          <Field label="最大轮次" hint={reviewLocked.has("max_turns") ? "被环境变量固定" : "ReAct 循环上限"}>
            <input
              type="number"
              value={form.max_turns}
              disabled={reviewLocked.has("max_turns")}
              onChange={(event) => setNum("max_turns", event.target.value)}
              className={INPUT_CLASS}
            />
          </Field>
          <Field
            label="审查语言"
            hint={
              reviewLocked.has("languages")
                ? "被环境变量固定"
                : "auto：目录里有 .py 审 Python、有 .java 审 Java"
            }
            locked={reviewLocked.has("languages")}
          >
            <select
              value={form.languages}
              disabled={reviewLocked.has("languages")}
              onChange={(event) => setText("languages", event.target.value)}
              className={INPUT_CLASS}
            >
              <option value="auto">auto（自动探测）</option>
              <option value="python">仅 Python</option>
              <option value="java">仅 Java</option>
              <option value="python,java">Python + Java</option>
            </select>
          </Field>
        </div>

        <div>
          <Field
            label="标称上下文窗口"
            hint={budgetLocked.has("context_window") ? "被环境变量固定" : "模型上限，仅校验与展示"}
            locked={budgetLocked.has("context_window")}
          >
            <input
              type="number"
              value={form.context_window}
              disabled={budgetLocked.has("context_window")}
              onChange={(event) => setNum("context_window", event.target.value)}
              className={INPUT_CLASS}
            />
          </Field>
          <Field
            label="工作预算"
            hint={budgetLocked.has("budget_window") ? "被环境变量固定" : "三区阈值按这个算，不是标称窗口"}
            locked={budgetLocked.has("budget_window")}
          >
            <input
              type="number"
              value={form.budget_window}
              disabled={budgetLocked.has("budget_window")}
              onChange={(event) => setNum("budget_window", event.target.value)}
              className={INPUT_CLASS}
            />
          </Field>
          <Field label="触发点" hint="按当前比例换算成 token">
            <span className="font-mono text-[10.5px] text-ink-500">
              {softTokens.toLocaleString()} / {hardTokens.toLocaleString()} / {abortTokens.toLocaleString()}
            </span>
          </Field>
          <Field label="API Key" hint="不经过网页，请改 .env">
            <span className="text-[10.5px] text-ink-500">
              {config.llm.configured ? "已配置" : "未配置"}
            </span>
          </Field>
        </div>
      </div>

      {exceedsModel && (
        <p className="mt-2 rounded bg-amber-50 px-2 py-1 text-[10.5px] text-amber-700">
          工作预算超过标称窗口，实际按标称值生效。
        </p>
      )}

      <div className="mt-3 flex items-center gap-2">
        <button
          type="button"
          onClick={submit}
          disabled={save.isPending}
          className="rounded bg-ink-900 px-3 py-1 text-[11px] text-white hover:bg-ink-800 disabled:opacity-50"
        >
          {save.isPending ? "保存中…" : "保存配置"}
        </button>
        {notice && (
          <span
            className={clsx(
              "text-[10.5px]",
              notice.kind === "ok" ? "text-emerald-600" : "text-red-600",
            )}
          >
            {notice.text}
          </span>
        )}
      </div>

      <p className="mt-2 text-[9.5px] leading-relaxed text-ink-400">
        写回文件：{config.config_file}。保存会重写该文件，自定义注释不保留；
        被环境变量或 .env 固定的字段不会提交，避免“改了没反应、重启又变回去”。
      </p>
    </div>
  );
}

function Field({
  label,
  hint,
  locked,
  children,
}: {
  label: string;
  hint?: string;
  locked?: boolean;
  children: ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-ink-100 py-1.5">
      <div className="min-w-0">
        <div className={clsx("text-[10.5px]", locked ? "text-ink-400" : "text-ink-600")}>
          {label}
          {locked && " · 锁定"}
        </div>
        {hint && <div className="truncate text-[9.5px] text-ink-400">{hint}</div>}
      </div>
      {children}
    </div>
  );
}

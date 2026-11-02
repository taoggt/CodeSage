import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";

import { api } from "../api/client";
import { useConfig, useHealth, useStats } from "../api/queries";
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
          这里是只读视图。修改配置请编辑项目根目录的 <code className="rounded bg-ink-100 px-1">codesage.toml</code> 或{" "}
          <code className="rounded bg-ink-100 px-1">.env</code>，然后重启服务——不在网页里写密钥，避免把文件写坏。
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

        <section className="mt-5 grid gap-4 lg:grid-cols-2">
          <div>
            <h2 className="text-[12px] font-medium text-ink-700">LLM</h2>
            <Table
              rows={[
                ["Base URL", String(config.data?.llm.base_url ?? "—")],
                ["强模型", String(config.data?.llm.model_strong ?? "—")],
                ["快模型", String(config.data?.llm.model_fast ?? "—")],
                ["温度", String(config.data?.llm.temperature ?? "—")],
                ["超时", `${config.data?.llm.timeout ?? "—"}s`],
                ["API Key", config.data?.llm.configured ? "已配置（不显示）" : "未配置"],
              ]}
            />
          </div>
          <div>
            <h2 className="text-[12px] font-medium text-ink-700">预算与压缩</h2>
            <Table
              rows={[
                ["上下文窗口", String(config.data?.budget.context_window ?? "—")],
                ["软阈值", `${Math.round((config.data?.budget.soft_ratio ?? 0) * 100)}%`],
                ["硬阈值", `${Math.round((config.data?.budget.hard_ratio ?? 0) * 100)}%`],
                ["熔断", `${Math.round((config.data?.budget.abort_ratio ?? 0) * 100)}%`],
                ["最大轮次", String(config.data?.review.max_turns ?? "—")],
                ["活跃窗口", `${config.data?.review.active_window_turns ?? "—"} 轮`],
              ]}
            />
          </div>
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

function Table({ rows }: { rows: [string, string][] }) {
  return (
    <table className="mt-1.5 w-full text-[10.5px]">
      <tbody>
        {rows.map(([key, value]) => (
          <tr key={key} className="border-b border-ink-100">
            <td className="py-1 text-ink-500">{key}</td>
            <td className="py-1 text-right font-mono text-ink-700">{value}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

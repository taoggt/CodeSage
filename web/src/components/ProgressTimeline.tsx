import clsx from "clsx";

import { useJobStore } from "../stores/job";

const PHASES = [
  { key: "scan", label: "扫描", hint: "收集文件、应用过滤" },
  { key: "static", label: "确定性分析", hint: "AST 规则 + Ruff/Bandit/radon" },
  { key: "llm", label: "ReAct 循环", hint: "LLM 复核候选 + 补充发现" },
  { key: "merge", label: "合并去重", hint: "驳回误报、跨来源合并" },
  { key: "persist", label: "落盘", hint: "写入历史记录" },
];

export function ProgressTimeline() {
  const phase = useJobStore((state) => state.phase);
  const status = useJobStore((state) => state.status);
  const counters = useJobStore((state) => state.counters);

  const currentIndex = PHASES.findIndex((item) => item.key === phase);
  const finished = status === "done" || status === "cancelled";

  const hint = (key: string): string => {
    if (key === "scan" && counters.files) return `${counters.files} 个文件`;
    if (key === "static" && counters.static_findings !== undefined)
      return `${counters.static_findings} 条候选`;
    if (key === "llm") {
      if (counters.turn) return `第 ${counters.turn} 轮 · ${counters.tool_calls || 0} 次工具`;
      return "等待模型响应";
    }
    if (key === "merge" && counters.findings !== undefined) return `${counters.findings} 条`;
    return "";
  };

  return (
    <ol className="space-y-1.5">
      {PHASES.map((item, index) => {
        const done = finished ? index <= (currentIndex < 0 ? 4 : currentIndex) : index < currentIndex;
        const active = !finished && index === currentIndex;
        return (
          <li key={item.key} className="flex items-start gap-2">
            <span
              className={clsx(
                "mt-[5px] h-2 w-2 shrink-0 rounded-full",
                done && "bg-emerald-500",
                active && "animate-pulse bg-blue-500 ring-4 ring-blue-100",
                !done && !active && "bg-ink-300",
              )}
            />
            <div className="min-w-0 flex-1">
              <div className="flex items-baseline justify-between gap-2">
                <span
                  className={clsx(
                    "text-[12.5px]",
                    active ? "font-medium text-ink-900" : "text-ink-600",
                  )}
                >
                  {item.label}
                </span>
                <span className="shrink-0 text-[10.5px] text-ink-400">{hint(item.key)}</span>
              </div>
              {active && <div className="text-[10.5px] text-ink-400">{item.hint}</div>}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

export function TokenGauge() {
  const counters = useJobStore((state) => state.counters);
  const used = counters.tokens || 0;
  const window = 64000;
  const ratio = Math.min(1, used / window);

  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between text-[10.5px] text-ink-500">
        <span>上下文 Token</span>
        <span>
          {used.toLocaleString()} / {window.toLocaleString()}
        </span>
      </div>
      <div className="relative h-1.5 overflow-hidden rounded-full bg-ink-200">
        <div
          className={clsx(
            "h-full rounded-full transition-all",
            ratio >= 0.95 ? "bg-red-500" : ratio >= 0.8 ? "bg-orange-500" : "bg-blue-500",
          )}
          style={{ width: `${Math.max(2, ratio * 100)}%` }}
        />
        <span className="absolute inset-y-0 left-[60%] w-px bg-white/70" />
        <span className="absolute inset-y-0 left-[80%] w-px bg-white/70" />
      </div>
      <div className="mt-1 flex justify-between text-[9.5px] text-ink-400">
        <span>60% 软压缩</span>
        <span>80% 强制压缩</span>
        <span>95% 熔断</span>
      </div>
    </div>
  );
}

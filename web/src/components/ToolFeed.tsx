import clsx from "clsx";
import { useEffect, useMemo, useRef } from "react";

import { useJobStore } from "../stores/job";

export function ToolFeed() {
  const events = useJobStore((state) => state.events);
  const boxRef = useRef<HTMLDivElement | null>(null);

  const rows = useMemo(
    () =>
      events
        .filter((event) =>
          ["tool_calls", "tool_result", "assistant_message", "compress", "finish", "cancelled", "persist_failed", "eval_case", "changed_fallback"].includes(
            event.type,
          ),
        )
        .slice(-80),
    [events],
  );

  useEffect(() => {
    const box = boxRef.current;
    if (box) box.scrollTop = box.scrollHeight;
  }, [rows.length]);

  if (rows.length === 0) {
    return (
      <div className="px-1 py-2 text-[11px] leading-relaxed text-ink-400">
        开始审查后，这里会实时显示 Agent 每一次工具调用与判断依据。
      </div>
    );
  }

  return (
    <div ref={boxRef} className="max-h-[280px] space-y-1 overflow-y-auto pr-1">
      {rows.map((event, index) => {
        const data = event.data || {};
        if (event.type === "tool_calls") {
          return (
            <div key={index} className="flex gap-1.5 text-[11px] leading-relaxed">
              <span className="mt-[1px] text-blue-500">▶</span>
              <span className="min-w-0">
                <span className="font-medium text-ink-700">
                  {(data.names || []).join(", ")}
                </span>
                {data.args?.[0]?.preview && (
                  <span className="ml-1 break-all text-ink-400">{data.args[0].preview}</span>
                )}
              </span>
            </div>
          );
        }
        if (event.type === "tool_result") {
          return (
            <div key={index} className="flex gap-1.5 pl-3 text-[11px] leading-relaxed">
              <span className="mt-[1px] text-emerald-500">✓</span>
              <span className="min-w-0 text-ink-500">
                <span className="text-ink-600">{data.name}</span>
                {data.chars ? <span className="text-ink-300"> · {data.chars} 字符</span> : null}
                <div className="truncate text-ink-400">{data.preview}</div>
              </span>
            </div>
          );
        }
        if (event.type === "assistant_message") {
          return (
            <div key={index} className="border-l-2 border-ink-200 pl-2 text-[11px] italic leading-relaxed text-ink-500">
              {data.preview}
            </div>
          );
        }
        if (event.type === "compress") {
          return (
            <div key={index} className="rounded bg-amber-50 px-2 py-1 text-[10.5px] text-amber-700">
              记忆压缩：移除 {data.removed} 条消息，保留 {data.kept} 条
            </div>
          );
        }
        if (event.type === "eval_case") {
          return (
            <div key={index} className="flex gap-1.5 text-[11px] text-ink-600">
              <span className={data.detected ? "text-emerald-500" : "text-red-500"}>
                {data.detected ? "✓" : "✗"}
              </span>
              <span>
                [{data.index}/{data.total}] {data.case_id}
                <span className="ml-1 text-ink-400">报告 {data.reported} 条</span>
              </span>
            </div>
          );
        }
        if (event.type === "persist_failed" || event.type === "cancelled") {
          return (
            <div key={index} className="rounded bg-amber-50 px-2 py-1 text-[10.5px] text-amber-700">
              {event.type === "cancelled" ? "已取消当前任务" : `落盘失败：${data.error}`}
            </div>
          );
        }
        if (event.type === "finish") {
          return (
            <div key={index} className="text-[10.5px] text-ink-400">
              模型结论：{data.message}
            </div>
          );
        }
        return (
          <div key={index} className={clsx("text-[10.5px] text-ink-400")}>
            {data.reason}
          </div>
        );
      })}
    </div>
  );
}

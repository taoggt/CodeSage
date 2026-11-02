import type { ReactNode } from "react";

export function EmptyState({
  title,
  hint,
  action,
}: {
  title: string;
  hint?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center">
      <div className="text-[13px] font-medium text-ink-600">{title}</div>
      {hint && <div className="max-w-md text-[11.5px] leading-relaxed text-ink-400">{hint}</div>}
      {action}
    </div>
  );
}

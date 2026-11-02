import clsx from "clsx";

import { severityMeta } from "../lib/severity";
import { scoreColor } from "../lib/severity";

export function SeverityBadge({ severity, compact }: { severity: string; compact?: boolean }) {
  const meta = severityMeta(severity);
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1 rounded border text-[10.5px] font-medium",
        compact ? "px-1 py-0" : "px-1.5 py-0.5",
        meta.text,
        meta.bg,
        meta.border,
      )}
    >
      <span className={clsx("h-1.5 w-1.5 rounded-full", meta.dot)} />
      {meta.label}
    </span>
  );
}

export function ScoreRing({ score, size = 64 }: { score: number; size?: number }) {
  const radius = (size - 8) / 2;
  const circumference = 2 * Math.PI * radius;
  const clamped = Math.max(0, Math.min(100, score));
  const dash = (clamped / 100) * circumference;
  const color = scoreColor(clamped);

  return (
    <div className="relative" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="#e2e8f0"
          strokeWidth={6}
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={color}
          strokeWidth={6}
          strokeLinecap="round"
          strokeDasharray={`${dash} ${circumference - dash}`}
        />
      </svg>
      <div className="absolute inset-0 grid place-items-center">
        <span className="text-base font-semibold" style={{ color }}>
          {clamped}
        </span>
      </div>
    </div>
  );
}

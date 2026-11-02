export function formatNumber(value: number | null | undefined): string {
  return Number(value || 0).toLocaleString("zh-CN");
}

export function formatCost(value: number | null | undefined): string {
  const cost = Number(value || 0);
  if (!cost) return "¥0";
  return `¥${cost.toFixed(cost < 0.01 ? 4 : 3)}`;
}

export function formatDuration(seconds: number | null | undefined): string {
  const value = Number(seconds || 0);
  if (value < 60) return `${value.toFixed(1)}s`;
  return `${Math.floor(value / 60)}m ${Math.round(value % 60)}s`;
}

export function formatPercent(value: number | null | undefined, digits = 1): string {
  return `${(Number(value || 0) * 100).toFixed(digits)}%`;
}

/** "2026-11-02 14:30:11"（本地时间字符串）→ 相对时间 */
export function relativeTime(text: string | null | undefined): string {
  if (!text) return "";
  const parsed = new Date(text.replace(" ", "T"));
  if (Number.isNaN(parsed.getTime())) return text;
  const delta = (Date.now() - parsed.getTime()) / 1000;
  if (delta < 60) return "刚刚";
  if (delta < 3600) return `${Math.floor(delta / 60)} 分钟前`;
  if (delta < 86400) return `${Math.floor(delta / 3600)} 小时前`;
  if (delta < 86400 * 30) return `${Math.floor(delta / 86400)} 天前`;
  return text.slice(0, 10);
}

export function shortPath(path: string, keep = 3): string {
  if (!path) return "";
  const parts = path.replace(/\\/g, "/").split("/");
  return parts.length <= keep ? path : `…/${parts.slice(-keep).join("/")}`;
}

export function basename(path: string): string {
  const parts = (path || "").replace(/\\/g, "/").split("/");
  return parts[parts.length - 1] || path;
}

export function lineRange(start: number, end: number): string {
  return start === end ? `第 ${start} 行` : `第 ${start}–${end} 行`;
}

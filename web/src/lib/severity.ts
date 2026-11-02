export interface SeverityMeta {
  label: string;
  zh: string;
  text: string;
  bg: string;
  border: string;
  dot: string;
  solid: string;
}

export const SEVERITY: Record<string, SeverityMeta> = {
  critical: {
    label: "P0",
    zh: "严重",
    text: "text-red-700",
    bg: "bg-red-50",
    border: "border-red-200",
    dot: "bg-red-500",
    solid: "#dc2626",
  },
  major: {
    label: "P1",
    zh: "重要",
    text: "text-orange-700",
    bg: "bg-orange-50",
    border: "border-orange-200",
    dot: "bg-orange-500",
    solid: "#ea580c",
  },
  minor: {
    label: "P2",
    zh: "次要",
    text: "text-amber-700",
    bg: "bg-amber-50",
    border: "border-amber-200",
    dot: "bg-amber-500",
    solid: "#d97706",
  },
  info: {
    label: "P3",
    zh: "提示",
    text: "text-blue-700",
    bg: "bg-blue-50",
    border: "border-blue-200",
    dot: "bg-blue-500",
    solid: "#2563eb",
  },
};

export const SEVERITY_ORDER = ["critical", "major", "minor", "info"] as const;

export function severityMeta(value?: string): SeverityMeta {
  return SEVERITY[value || "info"] ?? SEVERITY.info;
}

export function scoreColor(score: number): string {
  if (score >= 90) return "#059669";
  if (score >= 75) return "#d97706";
  return "#dc2626";
}

export const CATEGORY_ZH: Record<string, string> = {
  security: "安全",
  bug: "潜在 Bug",
  performance: "性能",
  quality: "代码质量",
  best_practice: "最佳实践",
};

export const SOURCE_ZH: Record<string, string> = {
  ast_rule: "自研规则",
  ruff: "Ruff",
  bandit: "Bandit",
  radon: "radon",
  llm: "LLM 推理",
};

export const VERDICT_ZH: Record<string, string> = {
  wrong: "误报",
  right: "确认",
  missed: "漏报",
};

export type Severity = "critical" | "major" | "minor" | "info";
export type Verdict = "wrong" | "right" | "missed";
export type LessonKind = "false_positive" | "false_negative";

export interface Finding {
  id: string;
  file: string;
  line_start: number;
  line_end: number;
  category: string;
  severity: Severity;
  severity_label?: string;
  title: string;
  description: string;
  suggestion: string;
  evidence: string;
  confidence: number;
  source: string;
  rule_id: string;
}

export interface TokenUsage {
  input_tokens: number;
  output_tokens: number;
  calls: number;
  total: number;
}

export interface ReviewDetail {
  target: string;
  score: number;
  findings: Finding[];
  duration_sec: number;
  usage: TokenUsage;
  turns: number;
  llm_enabled: boolean;
  stop_reason: string;
  compress_events: number;
  files_scanned: number;
  files_skipped: number;
  by_severity: Record<string, number>;
  by_category: Record<string, number>;
  metadata: Record<string, any>;
  feedback_by_finding: Record<string, FeedbackItem[]>;
  review: {
    id: number;
    project: string;
    created_at: string;
    llm_enabled: boolean;
    partial: boolean;
  };
}

export interface FeedbackItem {
  id: number;
  verdict: Verdict;
  note: string;
  created_at: string;
}

export interface ReviewListItem {
  id: number;
  target: string;
  target_display: string;
  project: string;
  project_name: string;
  score: number;
  findings_count: number;
  by_severity: Record<string, number>;
  duration_sec: number;
  tokens: number;
  cost_yuan: number;
  llm_enabled: boolean;
  partial: boolean;
  created_at: string;
}

export interface ReviewList {
  total: number;
  limit: number;
  offset: number;
  items: ReviewListItem[];
}

export interface TreeEntry {
  path: string;
  name: string;
  type: "dir" | "file";
  has_children?: boolean;
  is_python?: boolean;
  size?: number;
}

export interface WorkspaceInfo {
  root: string;
  root_raw: string;
  name: string;
  files: number;
  skipped: number;
  git: {
    is_repo: boolean;
    branch: string;
    changed_files: string[];
    changed_count: number;
  };
  analyzer_ready: Record<string, boolean>;
}

export interface BrowseEntry {
  name: string;
  path: string;
  is_dir: boolean;
}

export interface BrowseResult {
  path: string;
  parent: string | null;
  entries: BrowseEntry[];
  shortcuts: { label: string; path: string }[];
  at_root: boolean;
}

export interface FileCoverage {
  reported: boolean;
  findings: number;
  severity: Record<string, number>;
}

export interface SubprojectReview {
  id: number;
  project: string;
  project_name: string;
  relative: string;
  score: number;
  findings_count: number;
  created_at: string;
  age_human: string;
}

export interface WorkspaceStatus {
  root: string;
  latest_review: null | {
    id: number;
    score: number;
    findings_count: number;
    by_severity: Record<string, number>;
    created_at: string;
    age_human: string;
    llm_enabled: boolean;
    partial: boolean;
    target: string;
    files_scanned: number;
  };
  file_coverage: Record<string, FileCoverage>;
  stale: boolean;
  stale_files: string[];
  subproject_reviews?: SubprojectReview[];
}

export interface FileContent {
  path: string;
  total_lines: number;
  start: number;
  end: number;
  content: string;
  truncated: boolean;
  language: string;
  size: number;
}

export interface JobSnapshot {
  id: string;
  kind: "review" | "eval";
  status: string;
  phase: string;
  params: Record<string, any>;
  workspace: string;
  elapsed_sec: number;
  counters: Record<string, number>;
  last_seq: number;
  dropped: number;
  result: any;
  error: null | { code: string; message: string };
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
}

export interface Lesson {
  id: number;
  kind: LessonKind;
  project: string;
  pattern: string;
  content: string;
  hits: number;
  created_at: string;
}

export interface LessonPreview {
  block: string;
  chars: number;
  limit_chars: number;
  limit_items: number;
  injected: { text: string; chars: number }[];
  candidates: number;
  truncated_by_chars: boolean;
  fits: boolean;
}

export interface EvalResultRow {
  tag: string;
  mock: boolean;
  profile: string;
  cases: number;
  detected: number;
  recall: number;
  precision: number;
  false_positive_rate: number;
  f1: number;
  total_cost_yuan: number;
  duration_sec: number;
  cancelled: boolean;
  created_at: string;
}

export interface Health {
  status: string;
  version: string;
  store_ok: boolean;
  store_path: string;
  llm_configured: boolean;
  static_built: boolean;
  git_available: boolean;
  analyzers: { name: string; available: boolean }[];
}

export interface AppConfig {
  llm: Record<string, any>;
  budget: Record<string, any>;
  review: Record<string, any>;
  server: Record<string, any>;
  store_path: string;
}

export interface StatsOverview {
  reviews: number;
  feedback: Record<string, number>;
  feedback_total: number;
  lessons: number;
  pending_feedback: number;
}

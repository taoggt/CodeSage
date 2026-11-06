import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, qs } from "./client";
import type {
  AppConfig,
  BrowseResult,
  EvalResultRow,
  FileContent,
  Finding,
  Health,
  Lesson,
  LessonPreview,
  ReviewDetail,
  ReviewList,
  StatsOverview,
  TreeEntry,
  WorkspaceInfo,
  WorkspaceStatus,
} from "./types";

export const useHealth = () =>
  useQuery({
    queryKey: ["health"],
    queryFn: () => api<Health>("/health"),
    refetchInterval: 30_000,
  });

export const useConfig = () => useQuery({ queryKey: ["config"], queryFn: () => api<AppConfig>("/config") });

export const useSaveConfig = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (patch: Record<string, unknown>) =>
      api<AppConfig>("/config", { method: "PUT", body: JSON.stringify(patch) }),
    onSuccess: (data) => {
      // 后端已热更新，直接把新配置写回缓存，不必再拉一次
      queryClient.setQueryData(["config"], data);
    },
  });
};

export const useStats = () =>
  useQuery({ queryKey: ["stats"], queryFn: () => api<StatsOverview>("/stats") });

export const useTree = (root: string, path: string, enabled = true) =>
  useQuery({
    queryKey: ["tree", root, path],
    queryFn: () =>
      api<{ entries: TreeEntry[] }>(`/workspace/tree${qs({ root, path, depth: 1 })}`),
    enabled: !!root && enabled,
  });

export const useBrowse = (path: string) =>
  useQuery({
    queryKey: ["browse", path],
    queryFn: () => api<BrowseResult>(`/workspace/browse${qs({ path })}`),
    retry: 0,
  });

export const useFile = (root: string, path: string | null) =>
  useQuery({
    queryKey: ["file", root, path],
    queryFn: () => api<FileContent>(`/workspace/file${qs({ root, path })}`),
    enabled: !!root && !!path,
  });

export const useWorkspaceStatus = (root: string) =>
  useQuery({
    queryKey: ["workspace-status", root],
    queryFn: () => api<WorkspaceStatus>(`/workspace/status${qs({ root })}`),
    enabled: !!root,
  });

export const useReviews = (params: { limit: number; offset: number; project?: string; q?: string }) =>
  useQuery({
    queryKey: ["reviews", params],
    queryFn: () => api<ReviewList>(`/reviews${qs(params)}`),
  });

export const useReview = (id: number | null) =>
  useQuery({
    queryKey: ["review", id],
    queryFn: () => api<ReviewDetail>(`/reviews/${id}`),
    enabled: !!id,
  });

export const useCompare = (a: number | null, b: number | null) =>
  useQuery({
    queryKey: ["compare", a, b],
    queryFn: () => api<any>(`/reviews/compare${qs({ a: a!, b: b! })}`),
    enabled: !!a && !!b,
  });

export const useLessons = (project: string, kind?: string) =>
  useQuery({
    queryKey: ["lessons", project, kind],
    queryFn: () =>
      api<{ items: Lesson[]; total: number; stats: Record<string, number>; pending: number }>(
        `/lessons${qs({ project, kind, limit: 100 })}`,
      ),
  });

export const useLessonPreview = (project: string) =>
  useQuery({
    queryKey: ["lesson-preview", project],
    queryFn: () => api<LessonPreview>(`/lessons/preview${qs({ project })}`),
  });

export const useFeedbackList = (project: string) =>
  useQuery({
    queryKey: ["feedback", project],
    queryFn: () => api<{ items: any[]; total: number }>(`/feedback${qs({ project, limit: 100 })}`),
  });

export const useEvalResults = () =>
  useQuery({
    queryKey: ["eval-results"],
    queryFn: () => api<{ items: EvalResultRow[] }>("/eval/results"),
  });

export const useEvalDetail = (tag: string | null) =>
  useQuery({
    queryKey: ["eval-result", tag],
    queryFn: () => api<{ summary: any; cases: any[] }>(`/eval/results/${tag}`),
    enabled: !!tag,
  });

export const useEvalDatasets = () =>
  useQuery({
    queryKey: ["eval-datasets"],
    queryFn: () => api<{ items: { path: string; name: string; cases: number }[] }>("/eval/datasets"),
  });

export function useOpenWorkspace() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (path: string) =>
      api<WorkspaceInfo>("/workspace/open", {
        method: "POST",
        body: JSON.stringify({ path }),
      }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["workspace-status"] });
    },
  });
}

export function useSubmitFeedback() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (payload: {
      finding_id: string;
      verdict: string;
      note?: string;
      review_id?: number;
    }) =>
      api<{ lesson_id: number; lesson_created: boolean; located: boolean }>("/feedback", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["review"] });
      client.invalidateQueries({ queryKey: ["lessons"] });
      client.invalidateQueries({ queryKey: ["lesson-preview"] });
      client.invalidateQueries({ queryKey: ["feedback"] });
      client.invalidateQueries({ queryKey: ["stats"] });
    },
  });
}

export function useAddLesson() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      api<{ id: number; dup?: boolean }>("/lessons", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["lessons"] });
      client.invalidateQueries({ queryKey: ["lesson-preview"] });
      client.invalidateQueries({ queryKey: ["stats"] });
    },
  });
}

export function useAddMissed() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      api<{ id: number; content: string }>("/lessons/missed", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["lessons"] });
      client.invalidateQueries({ queryKey: ["lesson-preview"] });
      client.invalidateQueries({ queryKey: ["stats"] });
    },
  });
}

export function useDeleteLesson() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api<{ deleted: number }>(`/lessons/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["lessons"] });
      client.invalidateQueries({ queryKey: ["lesson-preview"] });
    },
  });
}

export function useMineLessons() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (project: string) =>
      api<{ total: number; false_positive: number; false_negative: number; pending: number }>(
        "/lessons/mine",
        { method: "POST", body: JSON.stringify({ project: project || null }) },
      ),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["lessons"] });
      client.invalidateQueries({ queryKey: ["lesson-preview"] });
      client.invalidateQueries({ queryKey: ["feedback"] });
    },
  });
}

export function useResetMined() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (project: string) =>
      api<{ reset: number; pending: number }>("/lessons/reset-mined", {
        method: "POST",
        body: JSON.stringify({ project: project || null }),
      }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["lessons"] });
      client.invalidateQueries({ queryKey: ["lesson-preview"] });
      client.invalidateQueries({ queryKey: ["feedback"] });
    },
  });
}

export type { Finding };

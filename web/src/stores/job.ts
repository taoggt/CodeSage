import { create } from "zustand";

export interface JobEventItem {
  seq: number;
  type: string;
  data: any;
  at: number;
}

const STORAGE_KEY = "codesage.activeJob";

interface JobState {
  jobId: string | null;
  status: string;
  phase: string;
  counters: Record<string, number>;
  events: JobEventItem[];
  result: any;
  error: any;
  begin: (id: string) => void;
  apply: (type: string, data: any) => void;
  clear: () => void;
  hydrate: () => void;
}

const MAX_EVENTS = 400;

export const useJobStore = create<JobState>((set, get) => ({
  jobId: null,
  status: "idle",
  phase: "",
  counters: {},
  events: [],
  result: null,
  error: null,

  begin: (id) => {
    sessionStorage.setItem(STORAGE_KEY, id);
    set({
      jobId: id,
      status: "queued",
      phase: "",
      counters: {},
      events: [],
      result: null,
      error: null,
    });
  },

  apply: (type, data) => {
    const state = get();
    if (!state.jobId) return;

    if (type === "snapshot") {
      set({
        status: data.status ?? state.status,
        phase: data.phase ?? state.phase,
        counters: { ...state.counters, ...(data.counters || {}) },
      });
      return;
    }

    const entry: JobEventItem = {
      seq: Number(data?.seq ?? 0),
      type,
      data,
      at: Date.now(),
    };
    const events = [...state.events, entry].slice(-MAX_EVENTS);
    let status = state.status;
    let phase = state.phase;
    let counters = state.counters;
    let result = state.result;
    let error = state.error;

    switch (type) {
      case "queued":
        status = "queued";
        break;
      case "started":
        status = "running";
        break;
      case "phase":
        if (data?.name) phase = String(data.name);
        break;
      case "scan":
        counters = { ...counters, files: data?.files ?? 0, skipped: data?.skipped ?? 0 };
        break;
      case "static_done":
        counters = { ...counters, static_findings: data?.findings ?? 0 };
        break;
      case "context":
        counters = {
          ...counters,
          turn: data?.turn ?? counters.turn,
          tokens: data?.tokens ?? counters.tokens,
          context_ratio: data?.ratio ?? counters.context_ratio,
        };
        break;
      case "tool_calls":
        counters = {
          ...counters,
          tool_calls: (counters.tool_calls || 0) + (data?.names?.length || 0),
        };
        break;
      case "progress":
        counters = { ...counters, progress_done: data?.done ?? 0, progress_total: data?.total ?? 0 };
        break;
      case "eval_case":
        counters = {
          ...counters,
          case_index: data?.index ?? 0,
          case_total: data?.total ?? 0,
          recall: data?.recall ?? 0,
        };
        break;
      case "compress":
        counters = { ...counters, compress: (counters.compress || 0) + 1 };
        break;
      case "cancel_requested":
        status = "cancelling";
        break;
      case "cancelled":
        break;
      case "end":
        status = data?.status ?? "done";
        result = data;
        error = data?.error ?? null;
        sessionStorage.removeItem(STORAGE_KEY);
        break;
      default:
        break;
    }

    set({ status, phase, counters, events, result, error });
  },

  clear: () => {
    sessionStorage.removeItem(STORAGE_KEY);
    set({
      jobId: null,
      status: "idle",
      phase: "",
      counters: {},
      events: [],
      result: null,
      error: null,
    });
  },

  hydrate: () => {
    const saved = sessionStorage.getItem(STORAGE_KEY);
    if (saved) set({ jobId: saved, status: "running" });
  },
}));

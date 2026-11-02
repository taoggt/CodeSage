import { create } from "zustand";

import type { WorkspaceInfo } from "../api/types";

const ROOT_KEY = "codesage.root";
const NAME_KEY = "codesage.rootName";

interface WorkspaceState {
  root: string;
  name: string;
  currentFile: string | null;
  gitBranch: string;
  changedCount: number;
  setWorkspace: (info: WorkspaceInfo) => void;
  setFile: (path: string | null) => void;
  clear: () => void;
  hydrate: () => void;
}

export const useWorkspaceStore = create<WorkspaceState>((set) => ({
  root: "",
  name: "",
  currentFile: null,
  gitBranch: "",
  changedCount: 0,

  setWorkspace: (info) => {
    localStorage.setItem(ROOT_KEY, info.root);
    localStorage.setItem(NAME_KEY, info.name);
    set({
      root: info.root,
      name: info.name,
      gitBranch: info.git?.branch || "",
      changedCount: info.git?.changed_count || 0,
      currentFile: null,
    });
  },

  setFile: (path) => set({ currentFile: path }),

  clear: () => {
    localStorage.removeItem(ROOT_KEY);
    localStorage.removeItem(NAME_KEY);
    set({ root: "", name: "", currentFile: null, gitBranch: "", changedCount: 0 });
  },

  hydrate: () => {
    const root = localStorage.getItem(ROOT_KEY) || "";
    const name = localStorage.getItem(NAME_KEY) || "";
    if (root) set({ root, name });
  },
}));

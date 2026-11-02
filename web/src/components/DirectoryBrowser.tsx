import { useEffect, useState } from "react";
import clsx from "clsx";

import { useBrowse } from "../api/queries";

interface Props {
  open: boolean;
  initial?: string;
  onClose: () => void;
  onSelect: (path: string) => void;
}

function FolderIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden className="shrink-0">
      <path
        d="M3 7.5A2.5 2.5 0 0 1 5.5 5h4l1.8 2H18.5A2.5 2.5 0 0 1 21 9.5v8A2.5 2.5 0 0 1 18.5 20h-13A2.5 2.5 0 0 1 3 17.5v-10Z"
        stroke="#94a3b8"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function DirectoryBrowser({ open, initial, onClose, onSelect }: Props) {
  const [path, setPath] = useState("");

  useEffect(() => {
    if (open) setPath(initial || "");
  }, [open, initial]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const query = useBrowse(path);

  if (!open) return null;

  const data = query.data;
  const entries = data?.entries ?? [];

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-ink-900/30 p-6"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-label="浏览目录"
        className="flex h-[520px] w-[680px] max-w-full flex-col overflow-hidden rounded-lg border border-ink-200 bg-white shadow-xl"
      >
        <div className="flex items-center gap-2 border-b border-ink-100 px-4 py-2.5">
          <span className="text-[12.5px] font-medium text-ink-800">选择工作区文件夹</span>
          <span className="text-[10px] text-ink-400">只读取目录结构，不会修改任何文件</span>
          <button
            type="button"
            onClick={onClose}
            className="ml-auto rounded px-1.5 py-0.5 text-[11px] text-ink-400 hover:bg-ink-100 hover:text-ink-700"
          >
            关闭
          </button>
        </div>

        <div className="flex items-center gap-1.5 border-b border-ink-100 px-3 py-2">
          <button
            type="button"
            disabled={!data && !path}
            onClick={() => setPath(data?.parent || "")}
            className="rounded border border-ink-200 px-2 py-1 text-[10.5px] text-ink-600 hover:bg-ink-50 disabled:opacity-40"
          >
            ↑ 上一级
          </button>
          <button
            type="button"
            onClick={() => setPath("")}
            className="rounded border border-ink-200 px-2 py-1 text-[10.5px] text-ink-600 hover:bg-ink-50"
          >
            根目录
          </button>
          <div
            className="min-w-0 flex-1 truncate rounded bg-ink-50 px-2 py-1 font-mono text-[10.5px] text-ink-500"
            title={data?.path || "根视图"}
          >
            {data?.path || "此电脑"}
          </div>
        </div>

        <div className="flex min-h-0 flex-1">
          <div className="w-[120px] shrink-0 border-r border-ink-100 p-2">
            <div className="mb-1 px-1 text-[9.5px] uppercase tracking-wide text-ink-400">快捷位置</div>
            <div className="space-y-0.5">
              {(data?.shortcuts ?? []).map((item) => (
                <button
                  key={item.path}
                  type="button"
                  onClick={() => setPath(item.path)}
                  className="block w-full truncate rounded px-1.5 py-1 text-left text-[10.5px] text-ink-600 hover:bg-ink-100"
                  title={item.path}
                >
                  {item.label}
                </button>
              ))}
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto p-1.5">
            {query.isLoading && <div className="p-3 text-[11px] text-ink-400">读取目录…</div>}

            {query.error && (
              <div className="p-3 text-[11px] text-red-600">
                {(query.error as Error).message}
              </div>
            )}

            {!query.isLoading && !query.error && entries.length === 0 && (
              <div className="p-3 text-[11px] text-ink-400">这里没有子文件夹</div>
            )}

            {entries.map((entry) => (
              <button
                key={entry.path}
                type="button"
                onClick={() => setPath(entry.path)}
                className={clsx(
                  "flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-[11.5px]",
                  "text-ink-700 hover:bg-blue-50 hover:text-blue-800",
                )}
                title={entry.path}
              >
                <FolderIcon />
                <span className="truncate">{entry.name}</span>
                <span className="ml-auto shrink-0 text-[9.5px] text-ink-300">进入 →</span>
              </button>
            ))}
          </div>
        </div>

        <div className="flex items-center gap-2 border-t border-ink-100 bg-ink-50/60 px-4 py-2.5">
          <div className="min-w-0 flex-1">
            <div className="text-[9.5px] text-ink-400">当前选择</div>
            <div className="truncate font-mono text-[10.5px] text-ink-600" title={data?.path}>
              {data?.path || "（请先进入一个文件夹）"}
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded border border-ink-200 bg-white px-2.5 py-1 text-[11px] text-ink-600 hover:bg-ink-50"
          >
            取消
          </button>
          <button
            type="button"
            disabled={!data?.path}
            onClick={() => data?.path && onSelect(data.path)}
            className="rounded bg-ink-900 px-3 py-1 text-[11px] text-white hover:bg-ink-800 disabled:opacity-40"
          >
            选择此文件夹
          </button>
        </div>
      </div>
    </div>
  );
}

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";

import { ApiError, api } from "../api/client";
import { useOpenWorkspace } from "../api/queries";
import type { WorkspaceInfo } from "../api/types";
import {
  displayPath,
  folderName,
  loadRecentRoots,
  normalizePath,
  pushRecentRoot,
  removeRecentRoot,
  samePath,
} from "../lib/recent";
import { DirectoryBrowser } from "./DirectoryBrowser";

interface Props {
  root: string;
  name: string;
  onOpened: (info: WorkspaceInfo) => void;
}

export function WorkspacePicker({ root, name, onOpened }: Props) {
  const [open, setOpen] = useState(false);
  const [manual, setManual] = useState(false);
  const [manualPath, setManualPath] = useState("");
  const [browserOpen, setBrowserOpen] = useState(false);
  const [dialogPending, setDialogPending] = useState(false);
  const [opening, setOpening] = useState(false);
  const [error, setError] = useState("");
  const [nativeMissing, setNativeMissing] = useState(false);
  const [recent, setRecent] = useState<string[]>([]);
  // 本轮会话内被删除的记录（服务端删除失败时也不会再冒出来）
  const [hidden, setHidden] = useState<string[]>([]);

  const boxRef = useRef<HTMLDivElement | null>(null);
  const mutation = useOpenWorkspace();

  const roots = useQuery({
    queryKey: ["workspace-roots"],
    queryFn: () => api<{ items: string[] }>("/workspace/roots"),
  });

  useEffect(() => {
    if (open) {
      setRecent(loadRecentRoots());
      setManualPath(root);
      setError("");
    }
  }, [open, root]);

  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(event.target as Node)) {
        setOpen(false);
        setManual(false);
      }
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const suggestions = useMemo(() => {
    const seen = new Map<string, string>();
    for (const item of [...recent, ...(roots.data?.items ?? [])]) {
      const key = normalizePath(item);
      if (!key || seen.has(key)) continue;
      if (root && samePath(item, root)) continue;
      if (hidden.includes(key)) continue;
      seen.set(key, item);
    }
    return [...seen.values()].slice(0, 5);
  }, [recent, roots.data, root, hidden]);

  async function openPath(path: string) {
    setError("");
    setOpening(true);
    try {
      const info = await mutation.mutateAsync(path);
      pushRecentRoot(info.root);
      onOpened(info);
      setOpen(false);
      setManual(false);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setOpening(false);
    }
  }

  async function removeRecent(path: string) {
    const key = normalizePath(path);
    setHidden((prev) => [...prev, key]);
    setRecent(removeRecentRoot(path));
    try {
      await api(`/workspace/roots?path=${encodeURIComponent(path)}`, { method: "DELETE" });
      roots.refetch();
    } catch {
      /* 服务端删除失败不影响本地隐藏 */
    }
  }

  async function pickNative() {
    setError("");
    setDialogPending(true);
    try {
      const result = await api<{ path: string | null; cancelled: boolean }>("/workspace/pick", {
        method: "POST",
        body: JSON.stringify({ initial: root || null }),
      });
      if (result.path) await openPath(result.path);
    } catch (err) {
      const apiError = err as ApiError;
      if (apiError.status === 501) {
        setNativeMissing(true);
        setBrowserOpen(true);
        setError("当前环境不支持系统对话框，已切换为「浏览目录」");
      } else {
        setError(apiError.message);
      }
    } finally {
      setDialogPending(false);
    }
  }

  return (
    <div ref={boxRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className={clsx(
          "flex w-full items-center gap-2 rounded border bg-white px-2 py-1.5 text-left transition-colors",
          open ? "border-blue-400 ring-2 ring-blue-50" : "border-ink-200 hover:border-blue-300",
        )}
        title={root || "选择工作区"}
      >
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[11.5px] font-medium text-ink-800">
            {name || "选择工作区"}
          </span>
          <span className="block truncate font-mono text-[9.5px] text-ink-400">
            {root || "点此选择本地文件夹"}
          </span>
        </span>
        <span className="shrink-0 text-[9px] text-ink-400">{open ? "▴" : "▾"}</span>
      </button>

      {open && (
        <div className="absolute left-0 right-0 top-[calc(100%+4px)] z-40 rounded-lg border border-ink-200 bg-white p-1.5 shadow-lg">
          {!manual ? (
            <>
              <MenuItem
                onClick={pickNative}
                title={dialogPending ? "等待系统对话框…" : "选择本地文件夹…"}
                hint={nativeMissing ? "本机不支持，请用浏览" : "打开系统目录选择框"}
                disabled={dialogPending || opening}
                primary
              />
              <MenuItem
                onClick={() => {
                  setBrowserOpen(true);
                  setOpen(false);
                }}
                title="浏览目录…"
                hint="在网页里逐层点选（含盘符/快捷位置）"
              />
              <div className="my-1 border-t border-ink-100" />
              {suggestions.length > 0 ? (
                <>
                  <div className="px-2 pb-1 pt-0.5 text-[9.5px] uppercase tracking-wide text-ink-400">
                    最近打开
                  </div>
                  {suggestions.map((item) => (
                    <div
                      key={item}
                      className="group flex items-center rounded hover:bg-ink-100"
                    >
                      <button
                        type="button"
                        disabled={opening}
                        onClick={() => openPath(item)}
                        title={item}
                        className="min-w-0 flex-1 px-2 py-1 text-left disabled:opacity-50"
                      >
                        <span className="block truncate text-[11px] text-ink-700">
                          {folderName(item)}
                        </span>
                        <span className="block truncate font-mono text-[9px] text-ink-400">
                          {displayPath(item)}
                        </span>
                      </button>
                      <button
                        type="button"
                        onClick={() => removeRecent(item)}
                        title="从最近打开中移除（不会删除磁盘上的文件夹）"
                        className="mr-1 shrink-0 rounded px-1 py-0.5 text-[9.5px] text-ink-300 transition-colors hover:bg-white hover:text-red-600"
                      >
                        删除
                      </button>
                    </div>
                  ))}
                  <div className="my-1 border-t border-ink-100" />
                </>
              ) : null}
              <MenuItem
                onClick={() => setManual(true)}
                title="手动输入路径"
                hint="网络盘或超长路径时使用"
              />
            </>
          ) : (
            <div className="p-1">
              <input
                autoFocus
                value={manualPath}
                onChange={(event) => setManualPath(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && manualPath.trim()) openPath(manualPath.trim());
                  if (event.key === "Escape") setManual(false);
                }}
                placeholder="D:/path/to/project"
                className="w-full rounded border border-ink-200 px-2 py-1.5 font-mono text-[10.5px] outline-none focus:border-blue-400"
              />
              <div className="mt-1.5 flex gap-1.5">
                <button
                  type="button"
                  disabled={!manualPath.trim() || opening}
                  onClick={() => openPath(manualPath.trim())}
                  className="flex-1 rounded bg-ink-900 py-1 text-[10.5px] text-white hover:bg-ink-800 disabled:opacity-50"
                >
                  {opening ? "打开中…" : "打开"}
                </button>
                <button
                  type="button"
                  onClick={() => setManual(false)}
                  className="rounded border border-ink-200 px-2 py-1 text-[10.5px] text-ink-600 hover:bg-ink-50"
                >
                  返回
                </button>
              </div>
            </div>
          )}

          {dialogPending && (
            <div className="mt-1 rounded bg-blue-50 px-2 py-1.5 text-[9.5px] leading-relaxed text-blue-700">
              系统对话框已在你桌面上打开，选好后会自动载入。
            </div>
          )}
          {error && (
            <div className="mt-1 rounded bg-amber-50 px-2 py-1.5 text-[9.5px] leading-relaxed text-amber-700">
              {error}
            </div>
          )}
        </div>
      )}

      <DirectoryBrowser
        open={browserOpen}
        initial={root}
        onClose={() => setBrowserOpen(false)}
        onSelect={(path) => {
          setBrowserOpen(false);
          openPath(path);
        }}
      />
    </div>
  );
}

function MenuItem({
  onClick,
  title,
  hint,
  disabled,
  primary,
}: {
  onClick: () => void;
  title: string;
  hint?: string;
  disabled?: boolean;
  primary?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={clsx(
        "block w-full rounded px-2 py-1.5 text-left transition-colors disabled:opacity-50",
        primary ? "hover:bg-blue-50" : "hover:bg-ink-100",
      )}
    >
      <span className={clsx("block text-[11px]", primary ? "font-medium text-blue-700" : "text-ink-700")}>
        {title}
      </span>
      {hint && <span className="block text-[9px] leading-snug text-ink-400">{hint}</span>}
    </button>
  );
}

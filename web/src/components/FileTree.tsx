import { useMemo, useState } from "react";
import clsx from "clsx";

import { useTree } from "../api/queries";
import type { FileCoverage, TreeEntry } from "../api/types";
import { severityMeta } from "../lib/severity";

interface Props {
  root: string;
  coverage: Record<string, FileCoverage>;
  current: string | null;
  onSelect: (path: string) => void;
}

function statusOf(entry: TreeEntry, coverage: Record<string, FileCoverage>) {
  const info = coverage[entry.path];
  if (!info) return { dot: "bg-ink-300", label: "未审查", count: 0, severity: "" };
  const severity = ["critical", "major", "minor", "info"].find(
    (key) => (info.severity?.[key] || 0) > 0,
  );
  if (info.findings === 0) {
    return { dot: "bg-emerald-400", label: "已审查，无问题", count: 0, severity: "" };
  }
  return {
    dot: severity ? severityMeta(severity).dot : "bg-amber-400",
    label: `${info.findings} 条发现`,
    count: info.findings,
    severity: severity || "info",
  };
}

function TreeNode({
  root,
  entry,
  depth,
  coverage,
  current,
  onSelect,
}: {
  root: string;
  entry: TreeEntry;
  depth: number;
  coverage: Record<string, FileCoverage>;
  current: string | null;
  onSelect: (path: string) => void;
}) {
  const [expanded, setExpanded] = useState(depth === 0);
  const isDir = entry.type === "dir";
  const children = useTree(root, isDir && expanded ? entry.path : "", isDir && expanded);
  const status = !isDir ? statusOf(entry, coverage) : null;

  if (isDir) {
    return (
      <div>
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          className="flex w-full items-center gap-1.5 rounded px-2 py-[3px] text-left text-[12.5px] text-ink-600 hover:bg-ink-100"
          style={{ paddingLeft: 8 + depth * 12 }}
        >
          <span className="w-3 text-[10px] text-ink-400">{expanded ? "▾" : "▸"}</span>
          <span className="truncate">{entry.name}</span>
        </button>
        {expanded && (
          <div>
            {(children.data?.entries ?? []).map((child) => (
              <TreeNode
                key={child.path}
                root={root}
                entry={child}
                depth={depth + 1}
                coverage={coverage}
                current={current}
                onSelect={onSelect}
              />
            ))}
          </div>
        )}
      </div>
    );
  }

  return (
    <button
      type="button"
      onClick={() => onSelect(entry.path)}
      title={status?.label}
      className={clsx(
        "flex w-full items-center gap-1.5 rounded px-2 py-[3px] text-left text-[12.5px]",
        current === entry.path
          ? "bg-blue-50 text-blue-700"
          : "text-ink-700 hover:bg-ink-100",
      )}
      style={{ paddingLeft: 8 + depth * 12 }}
    >
      <span className={clsx("h-1.5 w-1.5 shrink-0 rounded-full", status?.dot)} />
      <span className="truncate">{entry.name}</span>
      {!!status?.count && (
        <span className="ml-auto rounded bg-ink-100 px-1 text-[10px] text-ink-500">
          {status.count}
        </span>
      )}
    </button>
  );
}

export function FileTree({ root, coverage, current, onSelect }: Props) {
  const tree = useTree(root, "");
  const entries = useMemo(() => tree.data?.entries ?? [], [tree.data]);

  if (!root) {
    return <div className="p-4 text-xs text-ink-400">尚未打开工作区</div>;
  }
  if (tree.isLoading) {
    return <div className="p-4 text-xs text-ink-400">加载文件树…</div>;
  }
  if (tree.error) {
    return (
      <div className="p-4 text-xs text-red-600">
        {(tree.error as Error).message}
      </div>
    );
  }

  return (
    <div className="py-1">
      {entries.map((entry) => (
        <TreeNode
          key={entry.path}
          root={root}
          entry={entry}
          depth={0}
          coverage={coverage}
          current={current}
          onSelect={onSelect}
        />
      ))}
      {entries.length === 0 && (
        <div className="p-4 text-xs text-ink-400">没有可显示的源码文件</div>
      )}
    </div>
  );
}

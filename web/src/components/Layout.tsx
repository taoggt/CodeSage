import { NavLink, Outlet, useLocation } from "react-router-dom";

import { useHealth } from "../api/queries";
import { useWorkspaceStore } from "../stores/workspace";
import clsx from "clsx";

const NAV = [
  { to: "/", label: "工作台", exact: true },
  { to: "/history", label: "历史" },
  { to: "/lessons", label: "错题本" },
  { to: "/evals", label: "评测" },
  { to: "/settings", label: "设置" },
];

export function Layout() {
  const health = useHealth();
  const root = useWorkspaceStore((state) => state.root);
  const name = useWorkspaceStore((state) => state.name);
  const location = useLocation();

  return (
    <div className="flex h-screen flex-col bg-ink-50 text-ink-800">
      <header className="flex h-12 shrink-0 items-center gap-6 border-b border-ink-200 bg-white px-4">
        <div className="flex items-center gap-2">
          <span className="grid h-6 w-6 place-items-center rounded-md bg-ink-900 text-[11px] font-bold text-white">
            CS
          </span>
          <span className="text-sm font-semibold tracking-tight">CodeSage</span>
          <span className="rounded bg-ink-100 px-1.5 py-0.5 text-[10px] text-ink-500">
            local
          </span>
        </div>

        <nav className="flex items-center gap-1">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.exact}
              className={({ isActive }) =>
                clsx(
                  "rounded-md px-3 py-1.5 text-[13px] transition-colors",
                  isActive
                    ? "bg-ink-900 text-white"
                    : "text-ink-600 hover:bg-ink-100 hover:text-ink-900",
                )
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>

        <div className="ml-auto flex items-center gap-3 text-[11px] text-ink-500">
          {root && (
            <span className="max-w-[280px] truncate" title={root}>
              {name || root}
            </span>
          )}
          <span
            className={clsx(
              "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5",
              health.data?.llm_configured
                ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                : "border-ink-200 bg-ink-50 text-ink-500",
            )}
            title={health.data?.llm_configured ? "已配置 API Key" : "未配置 API Key，仅确定性分析"}
          >
            <span
              className={clsx(
                "h-1.5 w-1.5 rounded-full",
                health.data?.llm_configured ? "bg-emerald-500" : "bg-ink-400",
              )}
            />
            {health.data?.llm_configured ? "LLM 已启用" : "零 Token 模式"}
          </span>
        </div>
      </header>

      <main className="min-h-0 flex-1 overflow-hidden">
        <Outlet />
      </main>

      <footer className="flex h-7 shrink-0 items-center gap-4 border-t border-ink-200 bg-white px-4 text-[10.5px] text-ink-400">
        <span>v{health.data?.version ?? "—"}</span>
        <span className={health.data?.store_ok ? "" : "text-red-600"}>
          存储 {health.data?.store_ok ? "可写" : "不可用"}
        </span>
        <span className="truncate" title={health.data?.store_path}>
          {health.data?.store_path ?? ""}
        </span>
        <span className="ml-auto">
          {(health.data?.analyzers ?? [])
            .map((item) => `${item.name}${item.available ? "" : "(不可用)"}`)
            .join(" · ")}
        </span>
        <span className="hidden xl:inline">{location.pathname}</span>
      </footer>
    </div>
  );
}

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React, { Suspense, lazy } from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { Layout } from "./components/Layout";
import "./index.css";
import { Workbench } from "./pages/Workbench";

const Report = lazy(() => import("./pages/Report").then((module) => ({ default: module.Report })));
const History = lazy(() => import("./pages/History").then((module) => ({ default: module.History })));
const Lessons = lazy(() => import("./pages/Lessons").then((module) => ({ default: module.Lessons })));
const Evals = lazy(() => import("./pages/Evals").then((module) => ({ default: module.Evals })));
const Settings = lazy(() => import("./pages/Settings").then((module) => ({ default: module.Settings })));

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      staleTime: 3_000,
      retry: 0,
    },
  },
});

function PageFallback() {
  return (
    <div className="grid h-full place-items-center text-[11.5px] text-ink-400">加载中…</div>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Suspense fallback={<PageFallback />}>
          <Routes>
            <Route element={<Layout />}>
              <Route index element={<Workbench />} />
              <Route path="report/:id" element={<Report />} />
              <Route path="history" element={<History />} />
              <Route path="lessons" element={<Lessons />} />
              <Route path="evals" element={<Evals />} />
              <Route path="settings" element={<Settings />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Route>
          </Routes>
        </Suspense>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);

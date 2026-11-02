import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 构建产物直接落到 Python 包的 static/ 下，由 FastAPI 托管。
// 开发态用 Vite dev server（5173），/api 代理到本地 uvicorn（8787）。
export default defineConfig({
  plugins: [react()],
  base: "/",
  build: {
    outDir: "../src/codesage/server/static",
    emptyOutDir: true,
    chunkSizeWarningLimit: 700,
    rollupOptions: {
      output: {
        // 首屏只需要 React + CodeMirror；图表与次要页面按需加载
        manualChunks: {
          "vendor-react": ["react", "react-dom", "react-router-dom"],
          "vendor-query": ["@tanstack/react-query", "zustand"],
          "vendor-editor": [
            "@codemirror/state",
            "@codemirror/view",
            "@codemirror/language",
            "@codemirror/lang-python",
          ],
          "vendor-charts": ["recharts"],
        },
      },
    },
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8787",
        changeOrigin: false,
      },
    },
  },
});

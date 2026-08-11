/// <reference types="vitest" />

import { realpathSync } from "node:fs";
import { defineConfig, searchForWorkspaceRoot } from "vite";
import react from "@vitejs/plugin-react";

// 开发服务器把 /api 与 /ws 代理到后端；容器内由 nginx 承担同样代理。
export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes("node_modules")) return undefined;
          if (/node_modules\/(?:\.pnpm\/)?(?:react|react-dom|react-router|react-router-dom)@?/.test(id)) {
            return "vendor-react";
          }
          if (id.includes("echarts") || id.includes("zrender")) return "vendor-charts";
          if (
            id.includes("axios")
            || id.includes("@tanstack")
            || id.includes("zustand")
            || id.includes("dayjs")
          ) {
            return "vendor-data";
          }
          return undefined;
        },
      },
    },
  },
  test: {
    include: [
      "src/**/*.test.{ts,tsx}",
      "src/**/*.spec.{ts,tsx}",
    ],
    exclude: ["e2e/**", "tests/**", "node_modules/**", "dist/**"],
  },
  server: {
    port: 5173,
    fs: {
      allow: [
        searchForWorkspaceRoot(process.cwd()),
        realpathSync("node_modules"),
      ],
    },
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
      "/ws": {
        target: "ws://localhost:8000",
        ws: true,
      },
    },
  },
});

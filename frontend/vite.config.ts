/// <reference types="vitest" />

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 开发服务器把 /api 与 /ws 代理到后端；容器内由 nginx 承担同样代理。
export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          "vendor-react": ["react", "react-dom", "react-router-dom"],
          "vendor-antd": ["antd", "@ant-design/icons"],
          "vendor-charts": ["echarts", "echarts-for-react"],
          "vendor-data": ["axios", "@tanstack/react-query", "zustand", "dayjs"],
        },
      },
    },
  },
  test: {
    exclude: ["e2e/**", "node_modules/**", "dist/**"],
  },
  server: {
    port: 5173,
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

// frontend/vite.config.ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 统一通过代理转发到本地后端，便于开发时保持同源接口结构。
export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes("node_modules")) {
            return undefined;
          }

          if (id.includes("highlight.js")) {
            return "vendor-highlight";
          }

          if (id.includes("marked")) {
            return "vendor-markdown";
          }

          // react 与 recharts 及其完整依赖树必须同 chunk:
          // 拆开会产生循环 chunk 依赖,生产环境模块初始化顺序不定,
          // 导致 recharts 顶层 React.forwardRef 读到 undefined 而崩溃
          if (
            /[\\/]node_modules[\\/](react|react-dom|scheduler|react-.+|@reduxjs[\\/]toolkit|redux|use-sync-external-store|reselect|recharts|victory-vendor|d3-.+|decimal\.js-light|fast-equals|lodash-es|eventemitter3|clsx|es-toolkit|immer|tiny-invariant|react-router|@remix-run[\\/]router)/.test(
              id,
            )
          ) {
            return "vendor-react";
          }

          return "vendor";
        },
      },
    },
  },
  server: {
    port: 5178,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8100",
        changeOrigin: true,
      },
    },
  },
});

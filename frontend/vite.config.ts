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

          if (id.includes("react")) {
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

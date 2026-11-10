// frontend/vite.config.ts
/// <reference types="vitest" />
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

const REQUIRED_REACT_PACKAGES = [
  "react", "react-dom",
  "@tanstack/react-virtual", "@tanstack/virtual-core",
];
const REACT_PACKAGES = new Set([
  ...REQUIRED_REACT_PACKAGES, "react-is", "scheduler",
  "react-router", "react-router-dom", "recharts",
]);

function npmPackage(id: string) {
  const path = id.replace(/\\/g, "/");
  const index = path.lastIndexOf("/node_modules/");
  if (index === -1) return undefined;
  const [name, scopedName] = path.slice(index + "/node_modules/".length).split("/");
  return name.startsWith("@") ? `${name}/${scopedName}` : name;
}

function verifyVendorChunks(): Plugin {
  return {
    name: "verify-vendor-chunks",
    apply: "build",
    generateBundle(_, bundle) {
      const chunks = Object.values(bundle).filter((item) => item.type === "chunk");
      const reactChunk = chunks.find((chunk) => chunk.name === "vendor-react");
      const reactPackages = new Set(Object.keys(reactChunk?.modules ?? {}).map(npmPackage));
      for (const name of REQUIRED_REACT_PACKAGES) {
        if (!reactPackages.has(name)) this.error(`${name} must be bundled with React in vendor-react`);
      }
      const chunksByFile = new Map(chunks.map((chunk) => [chunk.fileName, chunk]));
      for (const vendor of chunks.filter((chunk) => chunk.name.startsWith("vendor") && chunk !== reactChunk)) {
        const pending = [...vendor.imports];
        const visited = new Set<string>();
        while (pending.length) {
          const file = pending.pop()!;
          if (file === reactChunk?.fileName) {
            const reactModuleIds = new Set(Object.keys(reactChunk.modules));
            const imports = Object.keys(vendor.modules).flatMap((id) =>
              (this.getModuleInfo(id)?.importedIds ?? []).filter((dependency) => reactModuleIds.has(dependency)),
            );
            this.error(`${vendor.fileName} has a static dependency on vendor-react: ${[...new Set(imports)].join(", ")}`);
          }
          if (visited.has(file)) continue;
          visited.add(file);
          pending.push(...(chunksByFile.get(file)?.imports ?? []));
        }
      }
    },
  };
}

// 统一通过代理转发到本地后端，便于开发时保持同源接口结构。
export default defineConfig(() => {
  let reactModules: Set<string> | undefined;
  return {
    plugins: [react(), verifyVendorChunks()],
    build: {
      rollupOptions: {
        output: {
          onlyExplicitManualChunks: true,
          manualChunks(id, { getModuleIds, getModuleInfo }) {
            // CommonJS 转换辅助代码被 React 和独立第三方包共同使用。
            if (id === "\0commonjsHelpers.js") return "vendor-runtime";

            // React 相关包及其静态依赖归到同一块，避免公共依赖又被归入 vendor。
            if (!reactModules) {
              reactModules = new Set();
              const pending = [...getModuleIds()].filter((moduleId) => REACT_PACKAGES.has(npmPackage(moduleId) ?? ""));
              while (pending.length) {
                const moduleId = pending.pop()!;
                if (reactModules.has(moduleId)) continue;
                reactModules.add(moduleId);
                pending.push(...(getModuleInfo(moduleId)?.importedIds ?? []));
              }
            }

            if (reactModules.has(id)) return "vendor-react";
            const name = npmPackage(id);
            if (name === "highlight.js") return "vendor-highlight";
            if (name === "marked") return "vendor-markdown";
            return name ? "vendor" : undefined;
          },
        },
      },
    },
    test: {
      environment: "happy-dom",
      include: ["src/**/*.test.{ts,tsx}"],
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
  };
});

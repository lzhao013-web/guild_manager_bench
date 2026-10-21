import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { searchForWorkspaceRoot } from "vite";

export default defineConfig({
    root: "web/workbench",
    base: "/workbench/",
    plugins: [react()],
    // 已打开的页面仍可能按需加载旧的带 hash 分包，构建时不能删除它们。
    build: { outDir: "dist", emptyOutDir: false },
    server: {
        fs: {
            allow: [
                searchForWorkspaceRoot(fileURLToPath(new URL(".", import.meta.url))),
                fileURLToPath(new URL("../assets/icons/equipment", import.meta.url)),
            ],
        },
        proxy: Object.fromEntries(
            [
                "/api",
                "/replay",
                "/leaderboard",
                "/legacy",
                "/assets",
                "/app.js",
                "/styles.css",
            ].map((path) => [path, "http://127.0.0.1:8000"]),
        ),
    },
    test: {
        environment: "jsdom",
        setupFiles: ["./src/test-setup.ts"],
        include: ["src/**/*.test.{ts,tsx}"],
    },
});
